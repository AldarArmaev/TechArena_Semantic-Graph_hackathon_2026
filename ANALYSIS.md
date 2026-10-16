# Технический анализ TechArena / Train Ticket

Дата исследования: 16 октября 2026 года. Исследована поставленная версия исходников; `skeleton.commit = 313886e99befb94be6cd45f085c98e0019f59829`, `schema = v6-skeleton/2.1`. Это метаданные скелета, а не независимо проверенный Git HEAD: корень пакета не является Git checkout.

На этом этапе создан только этот документ. Train Ticket, его сервисы, базы и тесты приложения не запускались. Выполнены чтение исходников и данных, три штатных baseline на S0–S7, оценка их результатов, отдельный запуск BM25 с подбором `k` по S0 и диагностический обход скелета с открытыми входами S0. Solver не реализован. Диагностические JSON находятся вне проекта, в `C:/Users/PC/.codex/visualizations/2026/10/16/01a14295-4a74-79f1-ae1b-301b037012f5/`.

## 1. Вывод и рекомендуемое направление

Главный резерв качества — восстановление **условий активации конкретных call sites**, входных параметров и состояния между шагами. Транзитивное замыкание графа не решает эту задачу даже при известных входных обработчиках. На S0 штатный NLP baseline получил **0,430655**, а тот же closure с пятью правильными входами из gold — **0,487202**. Значит, исправить только NL → endpoint недостаточно.

Рекомендую гибридный solver: проверяемое сопоставление намерений с UI/API; AST/CFG для достижимого Java-кода; ограниченная интерпретация условий и значений; состояние по SQL dump; явные модели Spring Security, repository/ORM, RestTemplate и RabbitMQ; отдельный слой правил разметки. Каждый прогноз должен иметь доказательство: запрос → метод → достигнутое место вызова → выбранная цель, с условиями и происхождением данных. LLM, если используется, помогает разбирать текст и спорные фрагменты, но не придумывает ID и не выдаёт неограниченный набор рёбер.

Есть существенные неоднозначности исходного пакета. В частности, S0 содержит `JWTFilter`, но не его прямой вызов `JWTUtil`; содержит необычное `TokenServiceImpl.getToken → User.getPassword`; включает содержательные конструкторы ORM-сущностей, хотя справочник исключает конструкторы, вызванные библиотекой через отражение. SQL dump также расходится с кратким описанием контактов и баланса. Эти факты нельзя скрыть за «правильным» обходом. Ниже отделены подтверждённые свойства кода, наблюдения по gold и гипотезы о разметке.

Нельзя достоверно обещать численный score на S1–S7: их эталонов нет. Числа скрытых результатов в `REFERENCE.md` — результаты организаторов, а не наши измерения.

## 2. Формализация задачи и метрики

Для каждого сценария `s` дан упорядоченный текстовый список действий `a₁…aₙ`. Требуется восстановить последовательность запросов `qᵢ = (verb, path, body, headers, actor)`. Согласно условиям **один шаг соответствует одному клиентскому запросу через gateway**. Исходное состояние `D₀` одинаково для всех сценариев, но внутри сценария:

`(Dᵢ, responseᵢ, eventsᵢ) = AbstractExecute(source, qᵢ, Dᵢ₋₁, environment)`.

Это описание предлагаемой статической модели, не запуск Java-приложения. Итог — объединение событий всех шагов с удалением повторов:

| Отношение | Точное значение |
|---|---|
| `executes` | `scenario:Sx → methodID`: метод проекта выполнился хотя бы один раз. Не только controller/service. |
| `calls` | `callerMethodID → actualCalleeMethodID`: направленный вызов с реально выбранной реализацией. |
| `remote` | `sendingMethodID → receivingMethodID`: активированный HTTP/message переход между сервисами. |
| `data` | `repositoryCallingMethodID → tableID`: SQL к конкретной таблице, включая отложенную ORM-операцию, отнесённую к исходному методу. |

Повторы, число вызовов и порядок не оцениваются. Ошибка не отменяет уже выполненные методы, обращения и изменения. Нет требования, чтобы каждый исполненный метод имел входящее `calls`: framework callbacks — отдельный механизм. Методы библиотек, сгенерированные Lombok методы и структура проекта не становятся рёбрами ответа.

Для отношения `r`: `Jᵣ = TPᵣ / (TPᵣ + FPᵣ + FNᵣ)`. Score сценария — `(J_executes + J_calls + J_remote + J_data)/4`; итог — среднее по **S1–S7**. S0 — только открытая разработческая проверка. Пустое предсказание при пустом gold даёт 1; хотя бы одно лишнее ребро при пустом gold даёт 0.

Практические следствия:

- Четыре категории имеют одинаковый вес, независимо от их размера. Единственный FP `remote` на S0 теряет **0,25 score сценария**. Это объясняет, почему более слабый по `executes/calls/data` closure оказался выше NLP на S0.
- Нельзя максимизировать recall без контроля precision. Также нельзя всегда выбирать только «обязательные» рёбра: пропущенные условные, но фактически активированные вызовы — FN.
- Решение об uncertain ветке должно учитывать весь её пакет последствий: новые методы, calls, remote, SQL; не голосовать за каждое ребро независимо.
- Сохранять условные альтернативы внутри анализа допустимо; объединять все альтернативы в сдачу недопустимо.
- Для ориентировочной модели выбора одного uncertain ребра порог ожидаемой полезности зависит от текущего IoU, а не фиксированно равен 0,5. При фиксированном текущем `TP=A`, размере объединения `U` и единственном кандидате порог `p > A/(U+A+1)`, приблизительно `J/(1+J)`. В реальной задаче gold неизвестен, события коррелированы и такого калиброванного `p` пока нет; это **не готовое правило thresholding**. Приоритет — устранять неопределённость исходниками и состоянием.

## 3. Карта входных данных

| Вход | Что фактически содержит | Как использовать |
|---|---|---|
| `README.md` | Контракт ответа, запреты, IoU, Rust bonus | Основная нормативная спецификация. |
| `REFERENCE.md` | Типы графа, правила SQL/framework, S0, особенности сценариев | Проверка преобразования графа и границ разметки. |
| `targets/trainticket/pom.xml` | 42 Maven module, Spring Boot 2.3.12.RELEASE, Spring Cloud Hoxton.SR12, Lombok и общие зависимости | Границы deployment, язык/фреймворк именно поставленной версии. |
| `targets/trainticket/*/src/main/` | 401 Java-файл, resources/config | Тела методов, условия, DI, модели данных, HTTP/message endpoints. |
| `targets/trainticket/*/src/test/` | 78 Java test files | Читать как дополнительную документацию поведения; в исследовании не запускались. |
| `targets/trainticket/ts-ui-dashboard/` | nginx + HTML + JS; несколько поколений UI | Восстановление намерения, API-запроса, полей и enum-кодов. |
| `skeleton/trainticket.json.gz` | `project/schema/commit/context/nodes/edges` | Каталог допустимых ID и кандидатов связей; не execution graph. |
| `scenarios/S0.json`…`S7.json` | `id/title/description/input/steps[{n,action}]` | Разбирать описание целиком и каждый шаг, связывать «тот же», «новый», «первый», «второй». Нет готовых HTTP bodies. |
| `context/README.md` | Учётная запись, дата, профиль и правила выбора | Семантические условия, отсутствующие в сценарии. |
| `context/db-after-warmup.sql.gz` | MySQL dump всех баз, 89 836 байт после распаковки | Точные строки, наличие записей, коллекции, статусы, цены и балансы. Не заменять InitData. |
| `gold/S0.json` | Каталоги 1538 методов/33 таблиц; `scenarios_all`; только один gold-блок S0 | Обучение правилам разметки и regression. `entry` — диагностическое поле, не категория score. |
| `platform/baseline_*.py` | Retrieval/closure-примеры | Переиспользуемые утилиты и измеримый исходный уровень. |
| `platform/evaluate_golden.py` | Проверка формата, ID, IoU, diagnostics | Локальная оценка S0. Для S1–S7 только format-only. |
| `examples/{bm25,closure,nlp}.json` | Ответы на все восемь сценариев | Формат и воспроизводимость примеров, не скрытые эталоны. |

### 3.1. Контекст: подтверждённые данные и расхождения

Контекст задаёт **системную дату 15 сентября 2026**, дату поездок **1 октября 2026**, default Spring profile, MySQL 8, RabbitMQ, Nacos и отсутствие внешней сети. Это время моделируемого сценария, а не сегодняшняя дата компьютера. Без регистрации используется `fdse_microservice / 111111`, без verification code. SQL `ts_auth.auth_user` связывает его с ID `4d2a46c7-71cb-4cf1-b5bb-b68406d9da6f`.

Проверка dump по колонкам `CREATE TABLE`, а не по позиции «на глаз», дала:

| Таблица | Всего строк | Для этого пассажира | Существенный факт |
|---|---:|---:|---|
| `ts_contacts.contacts` | 5 | 4 | `Contacts_One`, `Contacts_Two`, `MRK-acontact-725`, `MRK-adm-168`. Описание «один контакт» неточно. |
| `ts_order.orders` | 10 | 9 | Статусы: 0×4, 1×1, 3×1, 4×2, 6×1. |
| `ts_order_other.orders_other` | 8 | 7 | Статусы: 0×1, 1×3, 3×1, 4×1, 6×1. |
| `ts_consign.consign_record` | 13 | 11 | Список непуст, чтение багажа не требует его создания в S0. |
| `ts_inside_payment.inside_money` | 7 | 5 | Сумма `money = 10361.00`, включая пополнение 10000 и возвраты. |
| `ts_inside_payment.inside_payment` | 12 | 8 | Сумма `price = 1670.0`. |

По **реальному коду `InsidePaymentServiceImpl.pay`** баланс для сравнения стоимости равен `10361 − 1670 = 8691.00`, а не 10000. В S1–S3/S5–S7 это всё ещё достаточный запас для описанных обычных платежей, но делать окончательный вывод следует после вычисления стоимости конкретного билета. В S4 новый user не наследует эти записи. Нельзя моделировать деньги отдельным полем «balance=10000», забывая ledger.

README/REFERENCE называют dump точным исходным состоянием. Для предварительного проектирования разумно считать его основным источником строк, а текст — источником намерений и даты. **Это предлагаемый приоритет, а не подтверждённое организаторами разрешение противоречия.** Оставить такой выбор явным и проверяемым.

Dump нельзя целиком открыть как строгий UTF-8: декодирование падает на смещении 20907 из-за настоящих байтов `_binary` в `ts_delivery.delivery`. Заголовок содержит `SET NAMES utf8mb4`; успешное декодирование как cp1251 не доказывает cp1251-кодировку. Нужен разбор SQL на уровне байтов, с MySQL escaping, binary literals и отдельным декодированием текстовых значений. Для проверки ASCII-таблиц в исследовании использовалось обратимое Latin-1 представление байтов; binary payload не интерпретировался.

Не следует запускать dump в MySQL или InitData для получения поведения. Нужен собственный читатель данных. `@OrderColumn` в route collections важен для порядка станций/расстояний; порядок INSERT основного набора не равнозначен SQL `ORDER BY`.

## 4. Архитектура Train Ticket, важная для решения

Скелет содержит **42 module: 41 сервис и общий `ts-common`**. `ts-common.deployment_units` перечисляет 41 сервис: один и тот же ID общего метода может исполняться в разных сервисах. Нельзя превращать обычный вызов `ts-common.StringUtils` в remote только потому, что module ID отличается. Нужен deployment context при анализе и дедупликация по canonical ID при выводе.

В корне Train Ticket есть дополнительные каталоги, например `ts-avatar-service`, `ts-news-service`, `ts-ticket-office-service`, `ts-voucher-service`, которые не являются модулями данного skeleton. Упоминания UI или gateway не дают права добавлять отсутствующие method IDs.

### 4.1. Основные подсистемы

| Подсистема | Код и роль |
|---|---|
| Web/gateway | `ts-ui-dashboard/nginx.conf`: `/api/v1/` → `ts-gateway-service:18888`; gateway `application.yml`: `Path` → `lb://...`, включая case-sensitive `orderOtherService`. |
| Auth/user | `ts-auth-service`: login, Spring Security, JWT и `auth_user/user_roles`; `ts-user-service`: пассажирский профиль и регистрация с remote созданием auth user. |
| Контакты | `ts-contacts-service`, repository по account/contact/document. |
| Поиск | `ts-travel-service` для G/D и `ts-travel2-service` для остальных; `ts-basic-service` собирает station/train/route/price. |
| Планирование | `ts-travel-plan-service` → `ts-route-plan-service`, обе разновидности travel, seat/train; cheapest/quickest/minStation и transfer. |
| Бронирование | `ts-preserve-service` / `ts-preserve-other-service`: security, contact, trip detail, basic, seat, order; затем условные assurance/food/consign. |
| Заказы | `ts-order-service.orders` и `ts-order-other-service.orders_other`, разные типы controller/DTO/entity/service. |
| Оплата | `ts-inside-payment-service` читает ledger, при недостатке денег зовёт локальный `ts-payment-service`; изменяет статус через order service. «External payment» не означает сеть за пределами пакета. |
| Жизненный цикл | `ts-execute-service` (в исходниках пакет **`execute.serivce`**, класс controller **`ExecuteControlller`**), cancel, rebook. Сохранять эти реальные имена. |
| Дополнения | assurance, food → train-food/station-food; consign → consign-price. |
| Messages | food `RabbitSend` → delivery `RabbitReceive` через `food_delivery`; preserve/notification используют `email`, но достижимость send отдельно проверяется. |
| Администрирование | admin-basic-info/admin-route/admin-travel/admin-order/admin-user — оркестраторы; часто remote в обычные сервисы, а не прямой SQL. |

### 4.2. Карта сценариев и механизмов

| Сценарий | Что отличает его от других и проверяет solver |
|---|---|
| S0 | Login, чтение контактов/двух order-баз/багажа, Paid-filter; remote отсутствует. |
| S1 | G/D, first class, train food + assurance + 2 kg consign, internal pay, collected → used, lookup extras. |
| S2 | Other, station food, без insurance/consign; paid → cancel/refund; второй заказ → cheaper second-class rebook. |
| S3 | Новый контакт и два заказа с разными опциями/seat class; 0.5 kg; список assurance types. |
| S4 | Регистрация → новый auth user → login; пустой ledger → payment-service; cancel/refund; Cancel-filter. |
| S5 | Три advanced режима, transfer через Suzhou, выбран regular cheapest; без опций, затем collect/board. |
| S6 | Admin создаёт station/train/route/price/trip; последующий поиск обязан видеть изменения; пассажир покупает новый G9101; admin читает обе базы заказов. |
| S7 | После collect: запрос refund, отказ cancel/rebook; изменение имени/телефона consignee без изменения веса; два date-filter запроса. |

## 5. Natural language → UI → HTTP → controller

### 5.1. Что следует извлекать

Нужна промежуточная запись действия: `intent`, actor/role, train family, object references, опции, фильтры, даты, значения и ожидаемый исход. «Без insurance», «Other, not high-speed», «same trip», «new order», «collected» — ограничения, а не шумовые слова.

Сначала строится каталог UI API sites: HTML label/event binding → JS function/callback → HTTP verb/path expression → request fields. Затем путь сопоставляется с gateway service и комбинацией class/method Spring mappings. Сопоставление только с названием Java-метода часто ошибается: `preserve` означает покупку/бронирование, `assurance` — страхование, `consignment` — багаж, `refound` — буквальная опечатка в refund endpoint.

Динамические `url: path` следует разрешать через вызывающую функцию. Например, `static/assets/js/index.js:116–122` передаёт разные paths в `queryForTravelInfo`; `client_ticket_book.js:511–520` выбирает preserve path по train ID. Окно из десяти строк вокруг URL для этого недостаточно. Проверять также закомментированный код, старые `/login` и неиспользуемые страницы. Статическое обнаружение URL не равнозначно дополнительному запросу сценария.

### 5.2. Проверенные соответствия

Ниже `API = /api/v1`. Названия методов сокращены **только для текста документа**; в ответе нужны полные ID скелета.

| Намерение | UI / HTTP | Входной обработчик |
|---|---|---|
| Login username/password | `assets/js/client_login.js:25–47`, POST `API/users/login`; admin — `assets/js/adminlogin.js:11` | `auth.controller.UserController#getToken` |
| Контакты account | `assets/js/client_ticket_book.js:49–58`, GET `API/contactservice/contacts/account/{accountId}` | `contacts.controller.ContactsController#findContactsByAccountId` |
| Создать контакт | `client_ticket_book.js:557–574`, POST `API/contactservice/contacts` | `ContactsController#createNewContacts` |
| G/D search | `assets/js/index.js`, POST `API/travelservice/trips/left` | `travel.controller.TravelController#queryInfo` |
| Other search | Тот же UI, POST `API/travel2service/trips/left` | `travel2.controller.Travel2Controller#queryInfo` |
| Trip detail | POST `API/travelservice/trip_detail` или `API/travel2service/trip_detail`, `TripAllDetailInfo` | Соответствующий `getTripAllDetailInfo` |
| Trip food menu | `client_ticket_book.js:185–194`, GET `API/foodservice/foods/{date}/{startStation}/{endStation}/{tripId}` | `foodsearch.controller.FoodController#getAllFood` |
| Stores at station | GET `API/stationfoodservice/stationfoodstores/{stationId}` | `food.controller.StationFoodController#getFoodStoresOfStation` |
| Book | `client_ticket_book.js:442–520`, POST `API/preserveservice/preserve` или `API/preserveotherservice/preserveOther` | `PreserveController#preserve` / `PreserveOtherController#preserve` |
| Refresh orders | `client_order_list.js:39–55`, POST `API/orderservice/order/refresh` или `API/orderOtherService/orderOther/refresh` | `OrderController#queryOrdersForRefresh` / `OrderOtherController#queryOrdersForRefresh` |
| Open order / price | GET `API/orderservice/order/{orderId}` / `API/orderservice/order/price/{orderId}`, Other-аналог | `getOrderById` / `getOrderPrice`; подтверждено controller mapping, UI-функция может только показывать уже полученную строку. |
| Pay | `client_order_list.js:payMyOrder`, POST `API/inside_pay_service/inside_payment` | `InsidePaymentController#pay` |
| Collect / board | GET `API/executeservice/execute/collected/{orderId}` / `.../execute/{orderId}` | `ExecuteControlller#collectTicket` / `executeTicket` |
| Refund quote / cancel | GET `API/cancelservice/cancel/refound/{orderId}` / `.../cancel/{orderId}/{loginId}` | `CancelController#calculate` / `cancelTicket` |
| Rebook | POST `API/rebookservice/rebook` | `RebookController#rebook`; дешевле не означает `payDifference`. |
| Consign account/order/update | `client_consign_list.js:queryMyConsign`, GET `API/consignservice/consigns/account/{accountId}`; GET `.../order/{orderId}`; PUT `.../consigns` | `ConsignController#findByAccountId` / `findByOrderId` / `updateConsign` |
| Consign price | GET `API/consignpriceservice/consignprice/{weight}/{isWithinRegion}` | `ConsignPriceController#getPriceByWeightAndRegion` |
| Assurance types / record | GET `API/assuranceservice/assurances/types` / `API/assuranceservice/assurance/orderid/{orderId}` | `getAllAssuranceType` / `findAssuranceByOrderId`; у lookup **assurance в единственном числе**. |
| Register | POST `API/userservice/users/register` | `user.controller.UserController#registerUser` |
| Advanced search | `client_adsearch.js:47–51`, POST `API/travelplanservice/travelPlan/{cheapest,quickest,minStation}` | `TravelPlanController#getByCheapest/getByQuickest/getByMinStation` |
| Transfer | POST `API/travelplanservice/travelPlan/transferResult` | `TravelPlanController#getTransferResult` |
| Admin create station/train/price | `assets/js/admin_station.js`, `admin_train.js`, `admin_price.js`; POST `API/adminbasicservice/adminbasic/{stations,trains,prices}` | `AdminBasicInfoController#addStation/addTrain/addPrice` |
| Admin route / trip / all orders | POST `API/adminrouteservice/adminroute`; POST `API/admintravelservice/admintravel`; GET `API/adminorderservice/adminorder` | `AdminRouteController#addRoute`, `AdminTravelController#addTravel`, `AdminOrderController#getAllOrders` |

Некоторые строки таблицы подтверждены mappings/DTO, но **не дают доказанного полного UI-клика**. Например, literal endpoint просмотра order/price имеется в сервере, а современный UI оперирует строкой заказа локально. Поэтому каталог должен хранить вид доказательства, а не объявлять все действия полноценными UI flows.

### 5.3. Важные несоответствия UI и контракту сценариев

1. `client_login.js` по умолчанию содержит `verifiCode='1234'` и запрещает пустой код. Контекст явно задаёт login **без кода**, S0 не включает remote verify. Значит, нельзя буквально проиграть UI defaults; UI служит для grounding API, параметры берутся также из контекста.
2. `client_order_list.queryMyOrderList()` отправляет **два** refresh-запроса; все три query flags задаёт `false`. Условия говорят один step = один request, а S0 различает family и Paid. Реальный UI wrapper не является атомарным шагом задания.
3. Поиск `enableStateQuery/enableTravelDateQuery/enableBoughtDateQuery` по frontend не обнаружил включения фильтров: найденные refresh builders выставляют `false`; status selector в admin — редактирование заказа. S0 Paid-body нужно восстанавливать по scenario + `OrderInfo` + controller/service, а не утверждать, что найден соответствующий UI обработчик фильтра.
4. `client_order_list.cancelOrder` блокирует collected до AJAX. S7 требует запросы с отказом системы; по контракту они должны достигнуть backend, даже если конкретный UI guard не позволяет клик. Не прогнозировать пустой шаг из frontend guard вопреки явному step=request.
5. Автоматические mounted callbacks, подгрузка assurance/menu/контактов и перезагрузка страницы могут делать дополнительные запросы в настоящем браузере. Условия не предписывают включать все такие загрузки; S0 не включает побочных API страницы book. Моделировать указанный запрос, а не весь браузер.

Это поддерживает трактовку «восстановить запрос, соответствующий действию»; точный механизм сценарного клиента в пакете отсутствует. Для неоднозначных шагов сохранять альтернативы и обоснование выбора, не добавлять все endpoints в итог.

## 6. Устройство skeleton graph

### 6.1. Фактический состав

Всего **15 229 nodes**, **19 573 edges**. `nodes` — словарь `ID → attributes`, `edges` — список `{source,type,target}`.

| Node kind | Количество | Полезные поля |
|---|---:|---|
| `module` | 42 | `name/path/artifact_id/deployment_units` |
| `file` | 442 | `path/module` |
| `type` | 512 | `fqn/type_kind/annotations/modifiers/extends_raw/implements_raw/file/line/end` |
| `method` | 1538 | `owner/name/descriptor/params_as_written/return/annotations/abstract/unresolved/module/file/line/end` |
| `parameter` | 2176 | `method/index/name/type/type_raw/annotations` |
| `field` | 1066 | `owner/name/type/type_raw/annotations/init_literal` |
| `call_site` | 8961 | `method/callee/receiver/receiver_type/arg_count/args_text/string_args/method_ref/file/line/col` |
| `config_entry` | 459 | `module/file/doc/key/value` |
| `data_resource` | 33 | `resource_kind/name/schema/origin` |

Методы: 1178 concrete/resolved, 342 abstract/resolved, 18 concrete с `unresolved=true`. `unresolved` — не разрешение выкинуть ID: любой ID надо сверять с `kind`, кодом и ролью. `?HttpHeaders` в canonical ID ContactsController — реальное содержимое скелета, хотя исходник использует wildcard-import Spring HTTP. **Не исправлять** ID до предполагаемой «правильной» Java-сигнатуры.

| Edge type | Количество | Фактические концы |
|---|---:|---|
| `calls` | 1240 | call_site → method |
| `runtime_dispatch` | 287 | call_site → method |
| `remote_call` | 415 | 166 call_site → handler; 249 config_entry → handler |
| `message_delivery` | 4 | call_site → listener method |
| `data_access` | 319 | call_site → data_resource |
| `contains` | 1302 | module → file, file → type/config_entry |
| `declares` | 11676 | type → method/field/nested type; **method → call_site** |
| `has_parameter` | 2176 | method → parameter |
| `references_type` | 1084 | method/parameter/field → type |
| `creates` | 749 | call_site → type |
| `implements` | 40 | implementing type → interface |
| `overrides` | 240 | implementing/overriding method → base/interface method |
| `extends` | 1 | type → base type |
| `depends_on` | 40 | module → common module |

В этой поставке **нет statement nodes, CFG, branch guards, value-flow edges**. У baselines есть код для `stmt:`, но здесь он не используется. Нельзя получить path sensitivity из самих 19 573 рёбер.

### 6.2. Как переводить кандидатов в события ответа

Пусть `c` — **достигнутый** call_site, `m = nodes[c].method`, `t` — выбранная цель.

- `calls(c,t)` или `runtime_dispatch(c,t)` → `calls(m,t)` только если этот вызов действительно выполнен и `t` — исполняемая проектная реализация. Объявление repository interface не является проектным runtime body.
- `remote_call(c,h)` → `remote(m,h)`, только если выбранные host, HTTP verb и путь приводят к `h`; затем анализировать обработчик в deployment context целевого сервиса.
- `remote_call(config,h)` — маршрутная подсказка. Не является отправкой от метода; клиентский gateway route не выводится как `remote`.
- `message_delivery(c,h)` → межсервисный `remote(m,h)` при достигнутой отправке и соответствующем listener. Дополнительный `calls(m,h)` не ставится.
- `data_access(c,T)` → `data(m,T)` при SQL effect. Таблица коллекции — отдельное ребро. Исходный owner сохраняется при deferred flush/lazy access согласно разметке, а не заменяется последним getter/controller.
- `executes(s,t)` добавляется для всех реально исполненных project methods, включая entry/filter/framework callbacks, даже если входящего проектного `calls` нет.

Структурные рёбра использовать для индексации, DI и принадлежности. `creates` не переносится в ответ как relation: нужен конкретный constructor method, если его исполнение учитывается и ID существует. `overrides` — направление реализация → база, но runtime `calls` идёт caller → реализация, не к интерфейсу и не в обратную сторону.

### 6.3. Проверенные ограничения проводки

- Из 24 gold calls S0 **пять** отсутствуют среди прямого преобразования `calls/runtime_dispatch`: четыре enum getter-вызова из двух Order constructors и `TokenServiceImpl.getToken → User.getPassword`. Enum-вызовы имеют call_site, но `receiver_type=None`; нужны enum/import resolution. Последнее ребро требует отдельного разбора разметки.
- `JWTFilter → JWTUtil.getJWTAuthentication` есть в calls скелета и в исходнике, но отсутствует в S0 gold.
- Все **семь** gold data-пар S0 действительно представлены после преобразования `data_access` по `call_site.method`. REFERENCE даёт более сильную гарантию для всех gold data; её можно использовать как жёсткий whitelist пар, **но не как признак активации**.
- Динамический `InsidePaymentServiceImpl.pay:59` имеет две remote-цели, order и orderOther. Код выбирает одну по `G/D` prefix. Closure включает обе.
- У 144 HTTP call sites с remote-проводкой распределение числа целей: 128×1, 15×2, 1×8. Восьмерная проводка у `RebookServiceImpl.deleteOrder:371`: реальный код формирует `/order/{id}` и использует **POST**, а скелет предлагает query/refresh/tickets/admin endpoints обеих order-служб. Это конкретный пример чрезмерного URL matching. Проверять reachable ли вообще `deleteOrder`, а при достижимости — реальный verb/path; не заменять опечатку POST на DELETE по названию метода.
- Из четырёх message_delivery одно соединяет `notification.RabbitSend → notification.RabbitReceive` **внутри одного module**. README определяет remote между разными сервисами, evaluator предупреждает о same-service remote. Скелет не является нормативным основанием включить это ребро; при достижимости нужен явный выбор политики/уточнение.
- Все 287 runtime_dispatch sites в данном скелете имеют также calls. Это текущий факт, а не гарантия для произвольного проекта.

## 7. Java/Spring: насколько реален статический анализ

### 7.1. Ветвления и значения

Большая часть критичных условий локальна и проста: `==`, `!=`, `&&/||`, null/empty, prefix `G/D`, статусы, `Response.status`, `Optional.isPresent`, `List.isEmpty`, числа/даты. Поэтому прикладная ограниченная интерпретация намного реалистичнее полного symbolic execution JVM.

Конкретные примеры:

- `TokenServiceImpl.getToken:65`: `!StringUtils.isEmpty(verifyCode)` решает, существует ли verify remote. `getServiceUrl` вызывается **до** if и остаётся executes/calls даже без remote.
- `orElseThrow(() -> new UserOperationException(...))` в `TokenServiceImpl:90–93`: lambda не исполняется при найденном пользователе. Синтаксический обход тела ошибочно включает exception constructor.
- `PreserveServiceImpl.preserve:178–234` и Other-аналог: assurance только при `assurance != 0`; food при `foodType != 0`; багаж при непустом `consigneeName`. Не от слова «insurance» в тексте, а от значения DTO после учёта отрицаний.
- First class — **код 2**, second — **3**, не 1/2: `ts-common/SeatClass.java`, UI `client_ticket_book.js:448–451`. Food type 0/1/2 = no/train/station.
- `OrderServiceImpl.queryOrders:125–189`: short-circuit OR флагов; даты преобразуются **внутри цикла при любом активном фильтре**, даже если date filters выключены. Это объясняет `String2Date` при Paid.
- `StringUtils.String2Date:17`: `str.length()` находится **до try**. Null вызывает NPE, а ошибка формата непустой строки попадает в catch и даёт epoch. Эти два случая имеют разные продолжения, несмотря на видимый catch(Exception).
- Там же travel-filter сравнивает travelDate с `boughtDateStart`, а не с travelDateStart. `OrderInfo.disableStateQuery` меняет `enableTravelDateQuery`, а не state flag. Интерпретатор обязан воспроизводить код, не предполагаемую бизнес-логику.

Достаточный начальный домен значений: константы/finite sets, Unknown с причиной, nullability, строки и шаблоны строк, enum-коды, numeric intervals, decimal values, даты, DTO fields, existence/cardinality коллекций и отношения идентичности объектов. Для бизнес-веток желательно вычислять конкретные значения из dump. Для случайного seat number использовать абстрактный диапазон и свойства, а не угадывать PRNG.

### 7.2. Вызовы, constructors и dispatch

Основной стиль — `@RestController` → `@Autowired Service interface` → единственная `@Service Impl` → repository/RestTemplate/helpers. 40 implements и 240 overrides дают полезный каркас. В `src/main` не найдены `@Primary`, `@Profile`, `FeignClient` или `EnableFeignClients`; явные `@Qualifier` встречаются в auth security и notification mail. Не нужен универсальный Feign engine для текущего корпуса.

Разрешать цель по call_site, типу receiver, deployment unit, DI field и сигнатуре. Нельзя собирать все реализации на уровне всего caller method. Метод-level стирание call_site особенно вредно, когда разные вызовы находятся под разными if.

Lombok-generated getter/setter может быть нужен для **вычисления поля**, но не должен порождать несуществующий method ID. Написанные вручную `OrderInfo.getState/isEnable…`, `User.getPassword` и enum `getCode` — реальные methods. Репозиторные abstract declarations имеют ID и статические calls, но runtime implementation библиотечная; не выводить interface в executes/calls, как делает BM25.

Нужны также chained invocations, overloads, enum constant receivers, nested/anonymous classes, lambdas и method references. `User.getAuthorities` использует stream/map `SimpleGrantedAuthority::new`; `TravelServiceImpl` содержит `Callable` и executor. Отложенный callback нельзя считать выполненным при создании lambda; executor body нужно учитывать лишь при достижении submit/get соответствующего API.

### 7.3. Framework callbacks и security

`auth/config/WebSecurityConfig.java` связывает `AuthenticationManagerBuilder` с `@Qualifier("userDetailServiceImpl")`, password encoder и JWTFilter. `authenticationManager.authenticate(upat)` в TokenService запускает проектный `UserDetailsServiceImpl.loadUserByUsername`, а затем проверки `UserDetails`. Эти методы не достижимы обычным calls closure из login.

Модель должна добавлять executions framework callbacks, их собственные SQL и прямые project→project calls внутри callbacks. **Не добавлять автоматически** `TokenService → UserDetailsServiceImpl` или `JWTFilter → controller`: их нет в S0 и это противоречит правилу непосредственного вызова. Startup `@Bean`, `configure`, InitData и enum initialization не относятся к сценарию только потому, что необходимы для работы приложения; они помогают настроить модель.

Отдельная проблема — S0 разметка JWTUtil и password edge; она подробно описана в разделе 9. Нужен слой annotation policy, чтобы уточнение этих правил не ломало семантический анализ бизнес-ветвей.

### 7.4. HTTP и межсервисные переходы

В текущем коде основной механизм — RestTemplate, в 70 main-файлах есть его упоминания. Распространённый helper `getServiceUrl(serviceName)` возвращает `"http://" + serviceName`; есть `DiscoveryClient`, но не следует из его вызова выводить project remote. Считать URI как выражение, переносить константы через локальные переменные и ветви, учитывать HTTP verb и canonical route.

Примеры разной семантики маршрутизации:

- `InsidePaymentServiceImpl.pay`, `SeatServiceImpl` выбирают order/other по prefix train ID: один путь.
- `CancelServiceImpl.cancelOrder/calculateRefund` и `execute.serivce.ExecuteServiceImpl.ticketCollect/ticketExecute` сначала запрашивают **high-speed order**, затем при not found — other. Для regular заказа реально исполняются **оба read-запроса**, включая SQL неуспешного поиска в первой базе. Нельзя оптимизировать это до одного «правильного сервиса» по train family.
- `AdminOrderServiceImpl.getAllOrders:39–82` безусловно читает оба сервиса.
- `TravelController.queryInfo:123` вызывает **`queryByBatch`**, а не похожий `query` и не `queryInParallel`. Выбор по имени search или всем overrides создаёт лишние пути.
- `user.service.impl.UserServiceImpl.saveUser:46–78` создаёт профиль и вызывает `createDefaultAuthUser`; тот делает POST `/api/v1/auth`. Логин нового пользователя в S4 зависит от обоих результатов.

При remote переносить сериализуемые значения DTO, identity user/order/trip и результат `Response.status/data`. Некоторые remote handlers могут выполниться при запросе с ошибкой; если route не совпал или security отказала раньше controller, сам handler ещё не исполнен. Нельзя считать любую сформированную строку HTTP фактом выполненного handler.

### 7.5. RabbitMQ и асинхронность

`foodsearch/config/Queues.java` и `delivery/config/Queues.java` задают `food_delivery`. `FoodServiceImpl.createFoodOrder:114–148` после проверки отсутствия дубликата, save и сериализации вызывает `sender.send`, затем `RabbitSend.send:21` делает convertAndSend. Правильный remote source — **RabbitSend.send**, target — **delivery.RabbitReceive.process**, не createFoodOrder и не repository. Listener сохраняет `Delivery` при успешном parse; SQL source — listener `process`.

Для foodType=1 и 2 sender находится вне внутреннего if(type==2): train-food тоже отправляет delivery. Без food до createFoodOrder не дойдут вообще. Это важнее общих ассоциаций «delivery = station food».

`preserve.config.Queues` и notification задают `email`, но в `PreserveServiceImpl.preserve:260`, Other:260, а также cancel email вызовы **закомментированы**. Наличие `sendEmail` и message_delivery в skeleton не доказывает уведомление при покупке/отмене. При этом preserve перед закомментированным send всё ещё выполняет `getAccount`, создаёт NotifyInfo и вызывает некоторые getters — нельзя удалять весь блок подготовки.

`notification.RabbitReceive.process` ловит ошибку mailService и **после catch сохраняет NotifyInfo**. Отсутствие внешней сети не означает отсутствие исполнения методов отправки, исключения или последующего SQL. Предел наблюдения асинхронных сообщений в gold S1–S7 не открыт: нельзя проверить, всегда ли listener успевает обработать сообщение. Допущение доставки для достигнутого валидного send следует фиксировать как модель, не выдавать за измерение.

`inside_payment.async.AsyncTask` имеет `@Async` и обращается к `rest-service-external`, но наличие класса не даёт его сценарной достижимости. Не добавлять его автоматически к обычной оплате: изученный `pay` использует синхронный RestTemplate payment-service.

### 7.6. Repository, ORM и SQL

Основной стиль — Spring Data CrudRepository; OrderRepository использует JpaRepository. Есть derived methods `findByAccountId`, `findByTravelDateAndTrainNumber`, `findByUsername`; native `@Query` для contacts/price/route/station/train. Старые Mongo-style `@Query` в comments не активны.

Пять entity-файлов содержат шесть `@ElementCollection`: User.roles, Route.stations/distances, TrainFood.foodList, StationFoodStore.foodList, FoodDeliveryOrder.foodList. `StationFoodStore` explicitly EAGER; User/Route/TrainFood имеют другие объявления, и **нельзя применять EAGER ко всему**. Route имеет `@OrderColumn`.

Модель должна различать:

- SQL select к основной таблице даже при **пустом результате**;
- отсутствие materialization/итераций при пустом результате;
- дополнительные SQL collection tables при фактической загрузке;
- save persist/merge, entity ID, отложенный flush, delete/transaction;
- first-level identity map и уже загруженные коллекции внутри persistence context;
- explicit method body, вызванный при ORM materialization, и исключаемые framework/generated методы;
- операции чтения и записи одной таблицы как **одно** data-ребро для данной пары source/target за весь сценарий.

Скелет гарантирует набор допустимых data-пар; первичная задача — выбирать активацию call_site и SQL effects. Но S0 показывает, что даже два `findByUsername` не дают одинаковый набор collection SQL: у UserDetailsService только auth_user, у TokenService также user_roles. Простое «каждый вызов repository читает все связанные таблицы» даёт FP. Причину такого распределения нельзя окончательно вывести из доступного gold и application config; нужна отдельная ORM/annotation policy и явно отмеченная неопределённость.

### 7.7. Состояние между шагами

Каждый сценарий получает независимую копию D₀. Внутри сценария нужны:

- символические новые UUID и связь user/contact/order/consign/auth user;
- переходы order: NOTPAID=0 → PAID=1 → COLLECTED=2 → USED=6; cancel=4, rebook/change=3 — по соответствующему коду;
- ledger платежей и возвратов, а не только итоговое число;
- наличие food/assurance/consign и исходные параметры;
- новые admin station/train/route/price/trip для S6;
- промежуточные response objects, чтобы «new order» не означал первый заказ warm-up.

После отказа S7 на collected состоянии нельзя исполнять нижние ветки refunds/rebooking/seat update; read-order, enum checks и controller/service до return учитываются. При изменении только consignee name/phone `ConsignServiceImpl.updateConsignRecord:97` не запрашивает новую цену, если вес сохранён. Если UI не передаст прежний вес, ветка изменится — поэтому тело PUT нужно восстановить, а не создать минимальный payload наугад.

Полезная оптимизация будущего анализатора — cache summaries по `(method, deployment, relevant argument abstraction, state predicates)`. Cache только по method ID некорректен: один и тот же method вызывается с разными flags/status/семействами в одном сценарии. Состояние при неоднозначности должно оставаться коррелированным между шагами; нельзя независимо объединять варианты order ID и статуса.

## 8. Подробный разбор S0

Gold содержит **34 executes, 24 calls, 0 remote, 7 data** и пять уникальных entry handlers для шести шагов. Ниже — разбор по коду с проверкой по gold, а не утверждение, что из gold восстановлен единственный возможный request body.

### 8.1. Шаги, параметры и ветви

| Шаг | Восстановленный запрос | Достигнутый код / эффект |
|---|---|---|
| 1. Log in | POST `/api/v1/users/login`, username/password из context, verificationCode отсутствует/пуст | `auth.UserController.getToken → TokenServiceImpl.getToken`; helper URL выполняется, verify HTTP не выполняется; authenticate через Spring; загрузка User; JWT. |
| 2. Contact book | GET `/api/v1/contactservice/contacts/account/4d2a46c7-71cb-4cf1-b5bb-b68406d9da6f`, Bearer token | `ContactsController.findContactsByAccountId → ContactsServiceImpl.findContactsByAccountId → repository.findByAccountId`; SQL contacts. |
| 3. G/D orders | POST `/api/v1/orderservice/order/refresh`; loginId пассажира, все query flags false | `OrderController.queryOrdersForRefresh → OrderServiceImpl.queryOrdersForRefresh → queryOrders`; SQL orders, непустой list; else без date parsing. |
| 4. Other orders | POST `/api/v1/orderOtherService/orderOther/refresh`; тот же account, query flags false | Other controller/impl `queryOrdersForRefresh → queryOrders`; SQL orders_other; непустой list; без `String2Date`. |
| 5. Paid orders | Тот же G/D refresh endpoint; `enableStateQuery=true`, `state=1`; остальные flags false | `queryOrders` входит в фильтрацию; выполняются state checks; в цикле стоят **пять последовательных String2Date call sites**, исполнение продолжается до ошибки/конца итерации. Точные date поля не выданы. |
| 6. Account luggage | GET `/api/v1/consignservice/consigns/account/{accountId}` | `ConsignController.findByAccountId` переводит ID в UUID → `ConsignServiceImpl.queryByAccountId` → repository; SQL consign_record. |

Выбор G/D для шага 5 — **вывод из gold**, а не явное утверждение сценария: у high-speed impl есть `queryOrders → String2Date`, у Other impl такого gold call нет. Поскольку оба списка в dump непусты и методы фильтрации аналогичны, это сильное свидетельство, что Paid применялся только к high-speed запросу. Не переносить эту догадку на любой неуточнённый refresh S1–S7 без учёта текущего train family и истории.

Точные `travelDateEnd/boughtDateStart/boughtDateEnd` для Paid не восстановимы однозначно. Если взять буквальные null defaults UI, `String2Date(null)` упадёт до своего catch. Gold содержит факт входа в `String2Date`, но не statement coverage или response, поэтому **не доказывает успешное завершение Paid-запроса**. Возможны и безопасные date strings, и ошибка после уже исполненных методов. Не надо выдумывать body, чтобы объяснить только набор рёбер; в будущей модели такие поля следует хранить как отдельную неопределённость.

### 8.2. Login: код, calls, callbacks, data

`auth/controller/UserController.java:41–51` непосредственно вызывает tokenService. `TokenServiceImpl.java:58–97`:

1. Читает DTO fields (Lombok methods не являются output vertices).
2. Вызывает свой `getServiceUrl("ts-verification-code-service")` вне if.
3. При пустом verificationCode пропускает RestTemplate exchange.
4. Вызывает library AuthenticationManager. Через настройки `WebSecurityConfig` достигается `UserDetailsServiceImpl.loadUserByUsername:26`, читающий `auth_user`.
5. Spring проверяет явно написанные `User.getAuthorities/getPassword/isAccountNonExpired/isAccountNonLocked/isCredentialsNonExpired/isEnabled`; все они есть в executes gold.
6. TokenService делает свой `userRepository.findByUsername`, вызывает `JWTProvider.createToken`; тот непосредственно вызывает `User.getUsername` и читает roles/userId через generated getters.
7. `orElseThrow` supplier не исполняется при существующем account; exception constructor в gold отсутствует.

У login **12 уникальных auth methods**. Gold calls — ровно пять: controller→TokenService.getToken, TokenService.getToken→свой URL helper, →JWTProvider.createToken, →User.getPassword, JWTProvider.createToken→User.getUsername. Из них password edge — спорная атрибуция, см. раздел 9. Нет calls TokenService→UserDetailsService; callback executions не надо соединять искусственно.

Gold data login — три направленные пары:

| Source method | Target table |
|---|---|
| `auth.security.UserDetailsServiceImpl#loadUserByUsername` | `res:table:ts-auth-service.auth_user` |
| `auth.service.impl.TokenServiceImpl#getToken` | `res:table:ts-auth-service.auth_user` |
| `auth.service.impl.TokenServiceImpl#getToken` | `res:table:ts-auth-service.user_roles` |

Не сливать первые две пары на основании одинаковой таблицы. Не добавлять четвёртую `UserDetailsServiceImpl→user_roles` только потому, что skeleton имеет такую проводку.

### 8.3. Orders: различия похожего кода

High-speed `OrderController.queryOrdersForRefresh:64–70` явно читает `OrderInfo.getLoginId`, затем dispatch в конкретный `OrderServiceImpl`. Его refresh вызывает queryOrders, собирает stationIds и возвращает список. Вызов `queryForStationId` **закомментирован**, поэтому remote station lookup отсутствует.

`OrderServiceImpl.queryOrders:125–189` читает repository по account, затем проверяет флаги. Ручные getters `OrderInfo` имеют ID и попадают в calls/executes. При flags=false все три проверки в OR исполняются, далее else читает getLoginId в логировании. При Paid state=true short-circuit сокращает OR, но внутри цикла по-прежнему проверяются travel/bought flags. Поэтому весь сценарий объединяет вызовы **getLoginId, getState и три isEnable…**. Log arguments тоже вычисляются Java-кодом; не исключать getter потому, что он находится в LOGGER.info.

Other `QueryInfo.java` имеет Lombok getters, в отличие от ручных high-speed `OrderInfo`: аналогичная бизнес-логика **не означает одинакового количества проектных getter methods**. В gold нет соответствующих `QueryInfo#getState/isEnable…`, поскольку такого ручного body в исходнике нет. Поэтому нельзя копировать шаблон рёбер между order-сервисами, заменив только module prefix.

`order.entity.Order#<init>` и `other.entity.Order#<init>` явно задают Date2String, FIRSTCLASS.getCode и PAID.getCode (`Order.java:75` и `other/Order.java:70`). Gold содержит оба constructor executions и по три исходящих calls от каждого. Наиболее естественное объяснение — materialization непустого repository result, но включение отражательных constructors требует отдельной политики из-за противоречия со справочником. **Нет** gold calls `queryOrders→Order.<init>`: repository/Hibernate является промежуточным framework caller.

Набор query SQL не меняется из-за Paid: фильтрация выполняется Java-циклом после `findByAccountId`, а не отдельным SQL WHERE state. За сценарий каждая `impl.queryOrders → соответствующая orders-table` — одно ребро, даже при повторном refresh.

### 8.4. Contacts и consign

`contacts/ContactsController.java:72` → `ContactsServiceImpl.java:41` → repository.findByAccountId; table `res:table:ts-contacts-service.contacts`.

`consign/ConsignController.java:46` → `ConsignServiceImpl.java:118` → repository.findByAccountId; table **`res:table:ts-consign-mysql.consign_record`**, не самодельное `ts-consign-service.consign_record` и не имя dump database `ts_consign`. UUID.fromString — библиотечный метод, не новое ребро.

Оба чтения выполняются без HTTP в другие сервисы. Метод, содержащий repository call, является data source, controller — только calls source.

### 8.5. Полный состав S0 в компактном виде

Группы executes: auth 12 + common 5 + contacts 2 + consign 2 + high-speed order 9 + Other order 4 = **34**. Common: JWTFilter, String2Date, Date2String, OrderStatus.getCode, SeatClass.getCode.

Calls: auth 5 + contacts 1 + consign 1 + high-speed order 12 + Other order 5 = **24**. Data: auth 3 + contacts 1 + consign 1 + order 1 + orderOther 1 = **7**. Remote: **0**.

Почему remote=0: gateway — клиентская маршрутизация; verifyCode пуст; station conversion в refresh выключен; остальное — локальные repository reads. Покупки, поиск, оплата, food и assurance в S0 вообще отсутствуют.

Приложение A ниже перечисляет **все canonical method IDs, все 24 calls и все семь data-пар**, без восстановления новых ID. Это позволяет проверять каждый элемент разбора против `gold/S0.json` и `nodes`.

## 9. Неоднозначности и противоречия, которые нельзя молча сгладить

| Наблюдение | Проверяемое основание | Вывод / что остаётся неизвестным |
|---|---|---|
| В gold есть JWTFilter, нет JWTUtil | `JWTFilter.java:25` непосредственно вызывает `JWTUtil.getJWTAuthentication`; calls skeleton это содержит; ни один метод JWTUtil не присутствует в S0 gold. | Нельзя объяснить отсутствием ветки: вызов безусловен в try. Это apparent contradiction с «все исполнившиеся проектные методы». Возможна особая instrumentation/annotation граница, но её нет в правилах. |
| `TokenService.getToken → User.getPassword` | Gold содержит эту пару. В `TokenServiceImpl:61` вызван **dto.getPassword**, receiver type BasicAuthDto; после загрузки User нет явного getPassword. В skeleton такой пары нет. | Spring действительно читает User password, но приписывание этому caller не следует из правила прямого project call. Это может быть свёртка library stack или особенность gold; не объявлять её обычным Java direct call. |
| Reflection constructors | Gold включает оба Order.<init> и исходящие calls; прямого new Order в этих query methods нет. REFERENCE исключает «геттеры и конструкторы, вызванные библиотекой лишь через отражение». | Возможно, имеется в виду исключение пустых/служебных constructors, а содержательные входят; буквальный текст этого не уточняет. Нужна явная политика, не «включить все entity constructors». |
| Auth collection SQL attribution | Оба auth findByUsername имеют data_access к auth_user и user_roles; gold даёт roles только TokenService. UserDetails.getAuthorities реально учитывается. | Важна lazy/persistence/атрибуция, но S0 не раскрывает точный механизм. Нельзя автоматически перенести таблицу ролей на каждый repository read. |
| Один шаг=request, UI делает несколько | `queryMyOrderList` отправляет два запроса; `mounted` подгружает данные; client login требует code; S7 UI отсекает cancellation. | Scenarios — API-действия, grounded в UI, а не буквальный браузерный replay. Точный клиент не дан. |
| Dump против текста context | Четыре контакта и вычисляемые 8691 против «один/10000». | Предпочтение dump для строк обосновано формулировкой «точное состояние», но остаётся расхождение. |
| Same-service MQ remote candidate | notification sender и listener в одном module; README remote только другого сервиса; evaluator выдаёт warning, не reject. | Не переносить message_delivery автоматически в remote. S0 без сообщений ничего не уточняет. |
| Date/first choice не задают request полностью | Paid-step не содержит date defaults; order выбор «первый» без SQL ordering; curator проверял альтернативы. | Использовать подтверждённую invariance по оцениваемым связям; не утверждать восстановление точных bodies/response order. |

Практический подход: отдельный реестр этих вопросов с source evidence и решениями политики. В будущем можно иметь два отчёта: semantic prediction по исходникам и prediction после подтверждённых annotation policies. Не подменять бизнес-анализ безусловным копированием S0 gold. При возможности уточнить у организаторов прежде всего JWTUtil/password/constructors и атрибуцию collections. Отсутствие ответа не даёт права запускать сервисы для разрешения вопросов.

## 10. Прогоны baselines и evaluation

### 10.1. Воспроизводимость

Запущены все три baseline со стандартными аргументами на S0–S7. Все три полученных JSON **полностью совпали как JSON-объекты** с `examples/*.json`. Для всех восьми блоков проверка по каталогу gold приняла ID/relations, отклонённых рёбер нет. Это не доказательство корректности рёбер скрытых сценариев.

PowerShell не разворачивает wildcard для Python так же, как bash. Воспроизводимые команды (вывод в отдельный каталог, не в исходники):

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$auditDir = 'C:/Users/PC/.codex/visualizations/2026/10/16/01a14295-4a74-79f1-ae1b-301b037012f5'
$scenarioFiles = (Get-ChildItem scenarios/*.json).FullName
python platform/baseline_bm25.py skeleton/trainticket.json.gz targets/trainticket $scenarioFiles -o "$auditDir/bm25.json"
python platform/baseline_closure.py skeleton/trainticket.json.gz $scenarioFiles -o "$auditDir/closure.json"
python platform/baseline_nlp.py skeleton/trainticket.json.gz targets/trainticket $scenarioFiles -o "$auditDir/nlp.json" --explain --gold gold/S0.json
python platform/evaluate_golden.py gold/S0.json "$auditDir/nlp.json"
python platform/evaluate_golden.py gold/S0.json --selftest
python platform/baseline_bm25.py skeleton/trainticket.json.gz targets/trainticket scenarios/S0.json -o "$auditDir/bm25-tuned.json" --gold gold/S0.json
python platform/evaluate_golden.py gold/S0.json "$auditDir/bm25-tuned.json"
```

Oracle-entry experiment: вызван штатный `baseline_closure.Index.closure(gold['scenarios']['S0']['entry'])`, результат упакован `Index.submission` и оценён `evaluate_golden.evaluate`. Это **диагностика качества traversal при открытых входах**, не новый solver и не результат независимого grounding.

### 10.2. Score на S0

| Вариант | executes IoU | calls IoU | remote IoU | data IoU | Среднее |
|---|---:|---:|---:|---:|---:|
| BM25, default k=150 | 0.057471 | 0.028571 | 0 | 0.083333 | **0.042344** |
| BM25, `--gold`, выбран k=50 | 0.090909 | 0.030303 | 0 | 0.117647 | **0.059715** |
| Closure, default steps | 0.388889 | 0.461538 | 1 | 0.375000 | **0.556357** |
| NLP+closure, default | 0.475000 | 0.533333 | 0 | 0.714286 | **0.430655** |
| Closure, правильные gold entry | 0.525000 | 0.566667 | 0 | 0.857143 | **0.487202** |

Подбор k использует тот же S0: это оптимизация на обучающем примере, не оценка обобщения. Его высокая/низкая цифра не предсказывает скрытый score.

Точная декомпозиция для стандартных baselines; gold sizes соответственно 34/24/0/7:

| Baseline | Relation | Pred | TP | FP | FN |
|---|---|---:|---:|---:|---:|
| BM25 | executes | 150 | 10 | 140 | 24 |
| BM25 | calls | 48 | 2 | 46 | 22 |
| BM25 | remote | 11 | 0 | 11 | 0 |
| BM25 | data | 32 | 3 | 29 | 4 |
| Closure | executes | 16 | 14 | 2 | 20 |
| Closure | calls | 14 | 12 | 2 | 12 |
| Closure | remote | 0 | 0 | 0 | 0 |
| Closure | data | 4 | 3 | 1 | 4 |
| NLP | executes | 25 | 19 | 6 | 15 |
| NLP | calls | 22 | 16 | 6 | 8 |
| NLP | remote | 1 | 0 | 1 | 0 |
| NLP | data | 5 | 5 | 0 | 2 |

С правильными entry closure даёт Pred/TP/FP/FN: executes **27/21/6/13**, calls **23/17/6/7**, remote **1/0/1/0**, data **6/6/0/1**.

### 10.3. Конкретные FP/FN NLP baseline

Grounding `--explain`: 91 UI API sites, 71 handler с UI context. Для S0 найдено четыре уникальных entry из пяти, **нет лишних entry**. Шаг 6 ошибочно выбрал ContactsController.findContactsByAccountId (score 2.00); правильный ConsignController.findByAccountId был вторым (1.85). На шаге 3 baseline берёт и order, и Other; в агрегате это маскируется тем, что step4 действительно использует Other. Нужно проверять grounding по шагам, а не только union entry.

**Шесть FP executes:** UserOperationException constructor; verify controller; verify service impl; CookieUtil.addCookie/getCookieByName/readCookieMap. Причины: неучтённая пустота verificationCode и ленивая lambda `orElseThrow`.

**Шесть FP calls:** TokenService→UserOperationException constructor; Other.queryOrders→String2Date; verify controller→verify impl; verify impl→CookieUtil.addCookie; verify impl→CookieUtil.getCookieByName; CookieUtil.getCookieByName→readCookieMap. Первые пять причинные ветки видны непосредственно в исходниках; последний pair проверен по множеству baseline/gold и CookieUtil graph. Other date parsing — потенциальная ветка, которая не активировалась в S0.

**Один FP remote:** TokenService.getToken→VerifyCodeController.verifyCode. Потеря четверти score, хотя remote «всего один».

**15 FN executes**, по причинам:

- Ошибка grounding багажа: ConsignController.findByAccountId и ConsignServiceImpl.queryByAccountId — 2.
- Spring authentication callbacks: UserDetailsServiceImpl.loadUserByUsername; User.getPassword/getAuthorities и четыре is… checks — 7.
- Framework filter: JWTFilter.doFilterInternal — 1.
- ORM constructor path: оба Order.<init>, Date2String, SeatClass.getCode, OrderStatus.getCode — 5.

**Восемь FN calls:** ConsignController→ConsignServiceImpl; спорный TokenService→User.getPassword; по три constructor→Date2String/SeatClass.getCode/OrderStatus.getCode у двух Order entities.

**Два FN data:** UserDetailsServiceImpl.loadUserByUsername→auth_user; ConsignServiceImpl.queryByAccountId→consign_record. FP data на S0 нет — не следует из этого, что SQL-closure точен на сценариях с optional creation/update.

### 10.4. Closure и BM25: отдельные причины ошибок

Closure выбирает entry `{ContactsController.findContactsByAccountId, OrderController.queryOrdersForRefresh, OrderOtherController.queryOrdersForRefresh, PriceController.query}`. Login ошибочно сопоставлен с price query; consign опять с contacts.

Его **два FP executes** — PriceController.query и PriceServiceImpl.findByRouteIdsAndTrainTypes. **Два FP calls** — price controller→price impl и Other.queryOrders→String2Date. **Один FP data** — price impl→price_config. **20 FN executes**: весь auth-набор 12, consign 2, JWTFilter 1, constructor/common effects 5. **12 FN calls**: auth 5, consign 1, constructors 6. **4 FN data**: auth 3, consign 1. Отсутствие verify remote здесь — следствие пропущенного login, а не понимания пустого verificationCode.

BM25 выбирает top-150 методов по общему тексту сценария; в том числе admin CRUD/configuration и abstract service declarations. У него всего два TP calls: high-speed и Other `queryOrdersForRefresh→queryOrders`. Три TP data — contacts/orders/orders_other. Login/consign и большая часть связей не восстановлены. Среди FP есть `AdminBasicInfoController.addContacts → AdminBasicInfoService.addContact` и assurance controller→abstract AssuranceService: формально ID валидны, но реальные тела interface не исполнялись. Fixed-k порождает большую массу лишних методов, даже когда сценарий короткий.

### 10.5. Устройство baseline и что переиспользовать

`baseline_bm25.py`: документы методов по классу/имени/body, top-k; edges только если выбран source и, для calls/remote, target. Нет CFG, state, dispatch correction, causal closure. Увеличение k увеличивает шум; настройка k на S0 не решает архитектуру.

`baseline_closure.Index`: извлекает HTTP mappings, переводит call_site→caller, CHA через overrides, deployment units, data/remote wiring. `closure` ходит по всем возможным callees без проверки call-site guards. Внутри цикла calls добавляет **весь method-level runtime_dispatch set** для каждого call target. В этой поставке больше одного уникального dispatch target на method найдено только у `inside_payment.init.InitData.run` (не сценарный вход); значит, это реальный дефект конструкции, но **не объяснение основных S0 ошибок**. Основная потеря — стирание call-site контекста и отсутствие callbacks/состояния.

`baseline_nlp.py`: полезные UI-context extraction, label/function vocabulary, BM25 normalization двух источников, CRUD synonyms; затем тот же closure. `NEG` удаляет отрицания как слова, вместо преобразования в поля DTO. Допуск `tau=0.05` берёт почти равные entries; admin penalty вычисляется по **всему сценарию**, поэтому в S6 наличие admin может засорить passenger шаги административными кандидатами. UI regex может читать comments и несколько поколений UI, не разрешает полноценный dataflow `url:path` и теряет связь события с конкретным branch.

Переиспользовать после проверки:

- gzip/JSON loading, canonical node lookup, renderer ответа, дедупликацию;
- HTTP mapping/path helpers как прототип, добавив Spring mapping/verb details, точность сегментов и gateway/module;
- deployment_units как конфигурационные данные;
- BM25/UI retrieval **как candidate generator**, с явным последующим reranking по role/family/intent/schema;
- formatter/evaluator и его TP/FP/FN diagnostics.

Заменить архитектурно:

- method-level unconditional closure → call-site/CFG evaluation;
- глобальный set entry → последовательность typed requests;
- deletion of negations → constraints/DTO values;
- implicit all-overrides → deployment/bean/receiver-specific dispatch;
- all data_access per reached method → SQL effects достигнутых repository sites + ORM policy;
- top-k executes → causal execution evidence;
- «все remote wiring» → symbolic URI/verb resolution и конкретные remote response values.

### 10.6. Evaluation: ограничения и обнаруженные дефекты selftest

`evaluate_golden._scenario` валидирует ID/kind/relation, дедуплицирует, затем сравнивает **направленные пары**. Wrong direction не совпадает; правильные IDs с неверной relation тоже не совпадают. Cross-module `calls` не запрещены валидатором, что необходимо для ts-common; same-module remote только warning. Отсутствующие executes для источников/целей тоже warning. Поэтому «принято» не равно «семантически корректно».

`--selftest` на поставленном односценарном gold дал **два FAIL**, остальные проверки OK:

1. Тест удаления единственного S0 ожидает scored=0 и warning, а `evaluate` возвращает format_only/score=None, когда ни одного оценённого блока нет. Сам тест ниже одновременно ожидает format_only для пустого scenarios.
2. Тест неверных relation names ждёт substring `remote` в rejected examples. В S0 remote gold пуст, неверные рёбра только data; реально отклонено ровно семь `relation`, как и ожидалось, но substring check падает.

Это дефекты selftest для данного gold, а не обнаруженная ошибка обычной формулы IoU. В исследовании platform files не исправлялись. Не использовать зелёный selftest как единственный критерий готовности. Для format-only CLI ожидаемый exit code — **3**, не «solver failed»; для scored — 0, invalid — 1. Ошибочные отдельные edges отражаются в rejected diagnostics, поэтому gate будущего solver должен требовать **rejected=0**, а не только успешный exit code.

## 11. Implications of Curator Clarifications

### 11.1. «Восстановить все рантайм зависимости без запуска проекта»

**Подтверждает:** README прямо требует учитывать входные данные, БД и последствия действий. «Все» относится к активированным зависимостям в заданном сценарии, а не ко всем потенциальным связям проекта. S0 с нулём remote при наличии verify wiring — проверяемый пример.

**Архитектура:** два разных графа: potential candidates и scenario event graph. Построение последнего требует ограниченной интерпретации кода/состояния и отдельной framework-модели. Читать/компилировать можно; запускать сервисы, HTTP-запросы к ним, собирать свои traces нельзя. Собственный статический evaluator не должен скрыто вызывать JVM application methods или InitData.

**Предупреждение:** обычный reachability/GraphRAG не восстанавливает runtime активацию. LLM-обзор архитектуры тоже не даёт execution evidence.

**Baselines:** closure/NLP явно вычисляют may-closure; BM25 вообще объявляет top-k methods executed. Все три нарушают эту семантику предсказания, хотя допустимы как низкие baseline. Запрет runtime они при штатном запуске соблюдают.

**Неоднозначность:** буквальное «все» конфликтует с S0 исключением JWTUtil и правилами reflected constructors. Нельзя из цитаты расширить gold до всех Java-callable методов; границу нужно уточнить.

### 11.2. «Сет содержит только то, что реально вызывалось… направление рёбер имеет значение»

**Подтверждает:** направленные пары в evaluator, `calls` source именно caller method, `remote` source sender, `data` source repository-calling method. Обратная пара — другой элемент множества. Лишний reverse edge — FP, а исходный пропущенный — FN.

**Архитектура:** canonical ordered edge `(source,relation,target)`, provenance каждого события, DI выбирает implementation. `overrides(impl,interface)` не задаёт направление call. Не добавлять симметричных рёбер и framework filter→controller.

**Предупреждение:** возможные зависимости, interface declarations, pipeline/callback «соседство» и совпадение классов не доказывают вызов. «Только реально вызывалось» утверждает отсутствие FP в gold, но само по себе не уточняет полноту и правила атрибуции.

**Baselines:** все три в целом сохраняют направление при call_site→method conversion и не симметризуют. BM25 оставляет calls к abstract interfaces; closure/NLP добавляют неактивные verify/lambda/date edges. У них основная проблема — активация/target selection, не переворот направления.

**Неоднозначность:** конкретное gold `TokenService→User.getPassword` не является прямым вызовом в исходнике, а `JWTFilter→JWTUtil` отсутствует. Цитата не разрешает эти расхождения автоматически. Возможно, «реально вызывалось» использует специальную проекцию трассы; описание этой проекции не дано.

### 11.3. «Даже если в одну бд, то это 2 разных ребра»

**Подтверждает:** identity ребра включает source method. В S0 два разных метода читают одну `auth_user`; это два data-ребра. Разные таблицы одной сущности — также разные targets.

**Архитектура:** dedup только по полному triple; не схлопывать по database, resource name или паре service/database. Таблицы брать из canonical nodes, включая `ts-consign-mysql`. Если два сервиса обращаются к одной физической БД, model должна сохранить каждого source и точный skeleton resource, не менять его на общий «MySQL».

**Предупреждение:** агрегация SQL на service→database теряет method ownership и коллекционные таблицы. В то же время десять запросов одного method к одной table — одно оцениваемое ребро.

**Baselines:** при преобразовании data_access все три сохраняют method→resource pairs и не схлопывают их до БД. Этот конкретный структурный failure mode **не наблюдается**. Но выбор всех таблиц при достижении method остаётся неверным, а callbacks могут быть пропущены.

**Неоднозначность:** цитата разговорно говорит «два»; число рёбер фактически определяется числом различных directed method/table pairs, не фиксируется числом сервисов. Это явно следует из schema и S0.

### 11.4. «Важнее именно восстановить связи»

**Подтверждает:** score состоит только из четырёх множеств edges; новый UI, визуализация, GraphRAG, общий агент и impact analysis не дают прямых баллов.

**Архитектура:** основное время — request grounding, call-site conditions, state и framework/SQL boundaries. Внутренний explain report полезен только как средство диагностики конкретных FP/FN. `executes` тоже edge-category и даёт четверть score: нельзя интерпретировать цитату как разрешение его не делать.

**Предупреждение:** потратить хакатон на generic graph browser, embedding всей codebase или agent orchestration без улучшения точных pairs.

**Baselines:** готовят именно edges; посторонней инфраструктуры мало. Поэтому их формат/инструменты годятся для старта, но наличие графа и retrieval не эквивалентно правильным связям. Нужно менять предсказатель, а не украшать output.

### 11.5. IoU/Jaccard

**Подтверждает:** формула README/REFERENCE и `evaluate_golden.iou` полностью согласована с цитатой. FP и FN входят в один знаменатель, четыре relation categories равновесны.

**Архитектура:** измерять TP/FP/FN **по каждому relation**, делать ablations отдельно для grounding, conditions, callbacks, dispatch, SQL. Для сомнительных веток оценивать цену всего подграфа и приоритет проверки. Слишком широкий closure и слишком узкое «только очевидные edges» оба проигрывают.

**Предупреждение:** рекламировать растущий recall как растущий score; выбирать k по количеству найденных методов; игнорировать пустой gold category. Особенно опасно добавить один speculative remote в S0.

**Baselines:** BM25 — 140 FP executes на S0; NLP — один FP remote теряет 0,25, несмотря на улучшение трёх других категорий; closure с ошибочным grounding получает 0,556, выше oracle-entry closure 0,487. Это не парадокс: лишняя ветка captcha в последнем рушит remote category.

**Неоднозначность:** формула и направление ясны. Остаются неопределённости **содержимого gold**, а не метрики. При локальной оценке S0 считается, в итоговом leaderboard — нет; не переносить локальное среднее на final score.

## 12. Основные источники FP/FN и трудные случаи

| Приоритет | Случай / источник | Тип ошибки без специальной модели | Где проверять |
|---|---|---|---|
| P0 | Неправильный API по сходным словам | Чужой подграф FP + нужный FN | NLP S0 consign→contacts; S1 search→consign; S4 register→contacts. |
| P0 | G/D versus Other и actor per step | Лишний второй сервис или admin CRUD | UI path selection; S6 mixed admin/passenger; pay/seat prefix checks. |
| P0 | Условия опций | Food/insurance/consign FP | PreserveServiceImpl и Other preserve. |
| P0 | БД после предыдущих шагов | Wrong status, refund/payment/rebook FP/FN | S4 fresh account, S6 new trip, S7 collected. |
| P0 | `Response.status`, not found и ранний return | Пропуск реального отрицательного probe или исполнение хвоста после ошибки | cancel/execute first order then Other. |
| P0 | Framework execution roots | Auth/filter/ORM FN | WebSecurityConfig, UserDetailsService, S0 gold. |
| P0 | Annotation boundary ambiguities | Семантически верные edges могут стать FP gold | JWTUtil/password/constructor discrepancies. |
| P1 | Lazy lambda/Optional | Exception constructor FP | TokenService.orElseThrow. |
| P1 | Short-circuit и getter side execution | FP/FN вызовов getCode/getState/isEnable | Order query flags; enum comparisons в cancel/rebook. |
| P1 | Enum receivers/imports | Пропущенные getCode calls | `Order` constructors, SeatClass/OrderStatus. |
| P1 | SQL без результата | FN data при not found, FP constructors при пустом select | Regular execute/cancel probe, первый покупатель G9101. |
| P1 | ORM collections/persistence context | FP всех collection tables либо FN lazy SQL | User.roles; route_stations/distances; train/station food. |
| P1 | URI variables + HTTP verb | Remote fanout FP или неверный handler | InsidePayment.pay; Rebook.deleteOrder POST. |
| P1 | Callback/message causality | Все listeners FP или связанные async SQL FN | Food create→RabbitSend→delivery listener. |
| P1 | Выключенный код | Notification/station remote FP | preserve/cancel sendEmail comments; order refresh station conversion. |
| P1 | DTO/generated/manual methods | Несуществующие IDs / abstract calls / FN ручных getters | OrderInfo versus QueryInfo; UserDetails methods. |
| P1 | Dates/null/defaults/timezone | Неучтённое исключение и неверный refund/time guard | String2Date; rebook.checkTime; context clock, S7 date filters. |
| P1 | Существующие orders/contact identifiers | Покупка не того объекта, загрязнение state | Warm-up содержит много orders; новый order хранить символически. |
| P2 | Random seat/iteration early exits | Невозможно выбрать точный branch при коллизии | SeatService.distributeSeat/isContained, Random. |
| P2 | HashMap/SQL ordering и «первый» | Неверная конкретная цена/route или лишние ветви | Context invariant choices; route @OrderColumn. |
| P2 | Framework-generated equals/hashCode/toString interactions | FN написанных body, FP Lombok methods | `Order.equals/hashCode`, contains checks, logging. |
| P2 | Потоки, callable и exceptions | Неактивный parallel path FP | Travel queryByBatch versus queryInParallel, AsyncTask. |
| P2 | Совместные module ID / одинаковые FQN | Remote вместо calls или перепутанные targets | ts-common, order/other/common Order. |
| P2 | Нет внешней сети | Неверное «ничего не выполнилось» | Notification catches mail exception then saves. |

Есть и случаи с малой разницей в observable edges: 0.5 kg versus 2 kg в consign-price меняет арифметическую ветку, но не обязательно методы, calls или data, потому что getters там сгенерированы. Нужно прогнозировать оцениваемые связи, а не число строк/веток. Аналогично first/second dish может не менять graph, что подтверждено условиями choices.

## 13. Возможные архитектуры solver

### A. Улучшенный retrieval + условно отфильтрованный closure

Каталог API/UI, словарь intent/синонимов, явные train/role/negation ограничения, closure с несколькими guards для captcha/options/status. Добавить framework roots и enum resolution как правила.

**Ожидаемая точность:** выше базового NLP при правильных rules, но ограничена: состояние и source-level execution order останутся приближёнными; в большом preserve/rebook graph много необработанных ветвей. На S0 можно искусственно получить почти идеальный результат, что ничего не доказывает о S2/S4/S6/S7.

**Сложность:** низкая/средняя; быстро даёт usable submission. **Риски:** список special cases разрастается в неявный interpreter; теряется корреляция состояния; правила по имени метода не замечают ранний return и отрицательный lookup. **Роль:** fallback и промежуточный milestone, а не рекомендуемый окончательный путь к максимальному score.

### B. Ограниченный интерпретатор с summaries и source-backed моделями — рекомендуется

Индексы skeleton + AST/CFG достижимых методов; typed NL requests; data/state evaluator; query/HTTP/message/framework summaries; branch-specific traversal; explicit annotation policy. Универсальны простые Java constructs и value propagation, а сложные library operations моделируются узкими adapters. Для самых крупных оркестраторов допустимы декларативные summaries, **восстановленные по source lines и условиям**, а не по ID сценария.

**Ожидаемая точность:** лучший баланс precision/recall именно для этого конечного корпуса. Устраняет основные проверенные S0 FP/FN, позволяет обрабатывать stateful hidden scenarios. Предел — неполнота запросов/разметки, ORM и random/async детали; это требует calibrated policies, а не обещания 1.0.

**Сложность:** средняя/высокая, но растёт по покрываемым конструкциям и reachable source, не по всей JVM. **Риски:** ошибки semantics adapters, слишком грубое merging, случайные exception paths, неверное model state. **Контроль:** provenance, coverage unsupported constructs, локальные contract tests и source review summaries.

### C. Полный межпроцедурный abstract/symbolic analyzer Java + Spring/ORM

Полноценные resolution/CFG/heap/points-to, model Spring container/security, repository SQL/JPA, сериализация, RPC и async. Возможна разрешённая компиляция и чтение bytecode для уточнения вызовов, с mapping обратно в существующие IDs. Generated Lombok methods использовать внутренне, не сдавать.

**Ожидаемая точность:** потенциально высокая при зрелой реализации, но достижение высокой recall не гарантирует Jaccard: conservative may-analysis всё равно создаёт FP. Даже идеальная Java semantics не устраняет противоречия gold и недоопределённые requests.

**Сложность:** очень высокая; дорогая интеграция framework semantics, path explosion, classpath/build setup. **Риски:** потратить хакатон на инфраструктуру, получить менее точный overapproximation, чем B. **Роль:** долгосрочное развитие, не первый deliverable.

| Критерий | A: guarded closure | B: bounded interpreter | C: full analyzer |
|---|---|---|---|
| Цена первого рабочего результата | Низкая | Средняя | Высокая |
| Precision опциональных ветвей | Средняя, только на покрытых правилах | Высокая при конкретных DTO/state | Зависит от path sensitivity, сама по себе не гарантирована |
| Состояние S4/S6/S7 | Хрупкие special cases | Явная последовательная модель | Возможно, но дорого |
| Missing skeleton calls | Точечные supplements | AST resolution + framework summaries | Полное resolution при готовом classpath |
| ORM/annotation ambiguity | Всё равно нужна policy | Изолированная policy | Всё равно нужна policy |
| Проверяемость | Хороша для малых guards, хуже при разрастании | Source provenance для каждого effect | Сложная infrastructure debugging |
| Риск overfitting S0 | Высокий | Средний; снижается source-derived semantics | Низкий для языка, остаётся для annotation/model calibration |
| Реалистичность на хакатоне | Высокая, ограниченное качество | **Наиболее разумная** | Низкая без готового framework analyzer |

### 13.1. Компоненты рекомендуемой архитектуры B

1. **Canonical catalog.** Все IDs/metadata из skeleton; indices module/type/method/file/line/callsite/route/table. Potential graph хранится по call_site без потери кандидатов.
2. **Request planner.** Scenario → typed ordered intent → конкретный API request и ссылки на response предыдущих шагов. UI и mappings дают доказательства. Role хранится per step, train family — по объекту, а не общим флагом сценария.
3. **Source IR.** AST methods с call sites, sequence, if/else, loops, return/throw, try/catch, deferred lambdas; source ranges связывают IR с skeleton nodes. Не считать regex полноценным CFG.
4. **Value/state engine.** Environment/heap DTO fields, scalar/collection abstractions, entity identity, query results, ledger, dates и state snapshots. Separate per scenario. Bounded path alternatives с сохранением constraints.
5. **Effect summaries.** Direct project call; DI dispatch; HTTP; MQ; repository query/save/delete; constructors/materialization; Spring authentication/filter; known library functions вроде Optional, String, BigDecimal, Date.
6. **Annotation policy.** Отдельно правила inclusion/exclusion library/generated/framework/constructors и спорной атрибуции. Каждое правило помечено: контракт, наблюдение S0, либо гипотеза. Не смешивать с выполнением if.
7. **Evidence ledger.** Для каждого event: step, method, call_site/source lines, path condition, relevant inputs/state, selected target, certainty/unknown reason. Suppressed branches тоже сохранять для диагностики.
8. **Answer projection.** Event ledger → unique canonical directed triples; whitelist data pairs; validate kinds, all S0–S7 blocks, executions для методов рёбер, zero rejected. Confidence/provenance не добавляются вместо required schema.

Начальный interpreter не обязан вычислять каждую строку Java. Разумно сначала покрыть primitives/DTO/queries/Response и source-derived summaries critical orchestration. Неподдержанный вызов должен оставлять явный Unknown effect, а не silently «возвращать success» или активировать весь closure.

## 14. Конкретный roadmap реализации после просмотра анализа

### Этап 0. Зафиксировать контракт и неопределённости

- Сделать fixtures S0 gold/catalog и baseline reports; не трогать source/gold.
- Зафиксировать annotation questions раздела 9 и политику dump versus prose.
- Ввести schema/provenance и тесты directed pairs/дедупликации/отсутствующего блока/пустой category. Сохранять отдельно parser warnings и correctness diagnostics.
- Решить вопрос языка до создания большого execution engine; критерий Rust в разделе 16.

**Критерий завершения:** понятна разница semantic model/annotation policy; evaluator и baseline результаты воспроизводятся; нет скрытого gold hardcode.

### Этап 1. Построить каталог API/UI и request representation

- Индексировать все Spring handlers и gateway routes, UI event/function/API chain, DTO fields и enum constants.
- Для каждого шага S0–S7 сформировать один typed request plan с источником доказательства; отдельно отметить ambiguous bodies и references.
- Удерживать actor, family, negations, опции, date filter и ожидаемый refusal. Не использовать усреднённый admin flag для всех шагов.
- Проверить пару dozens повторяющихся intents, особенно S1 detail/order/price/assurance record, S2 rebook, S4 register и S6 admin→passenger.

**Критерий:** S0 все пять entries верны и каждый из шести шагов grounded корректно; для остальных шагов есть source-backed endpoint/verb и object bindings, а не только top-N score.

### Этап 2. Read-only модель SQL/state

- Реализовать binary-safe dump reader с CREATE columns, multi-row INSERT, escaping и collection order. Не запускать SQL.
- Сопоставить dump database/table ↔ entity/repository ↔ canonical resource ID, отдельно consign naming.
- Поддержать query predicates реально используемых repository signatures, Optional/list results, materialization, generated field reads/writes.
- Ledger pay/refund, symbolic UUID, new object identities, copy initial state per scenario.

**Критерий:** воспроизводятся приведённые в разделе 3 counts/sums; независимые scenarios не наследуют изменения; «new order» и warm-up records не смешиваются.

### Этап 3. Call-site-sensitive IR и минимальная интерпретация

- AST/CFG для reachable method bodies; mapping line/col→call_site.
- Sequence, conditions, Java short-circuit, loops с `empty/nonempty`, return/throw/try-catch, lambda delay.
- Resolution handwritten methods/constructors/enums, DI конкретной реализации, common deployment context.
- Propagation DTO fields, Response.status/data, strings/URI, decimals/dates и null exceptions.

**Критерий:** на S0 отсутствуют неактивные captcha, exception supplier и Other date parser; hand-written OrderInfo getters учитываются правильно. Unsupported constructs перечислены явно.

### Этап 4. Framework и ORM execution policies

- Login AuthenticationManager → project callbacks как executions, без придуманных calls.
- Filter execution для задействованных request chains; решить JWTUtil discrepancy отдельно.
- ORM materialization содержательных constructors; policy generated/reflection methods; collection SQL ownership и lazy state.
- Supplemental missing enum calls из source resolution.

**Критерий:** каждый оставшийся S0 FP/FN классифицирован как engine bug, unresolved input или annotation ambiguity. Высокий S0-score достигается объяснимыми правилами, а не копированием lists.

### Этап 5. Remote и message adapters

- Resolve host/path/verb по достигнутому IR; выбрать одну цель динамического call_site при определённом prefix.
- Передавать DTO/response/state через HTTP boundaries; учитывать first-negative-probe fallback.
- Queue-name binding; достигнутый send → listener; SQL listener; catch-path после mail failure.
- Отсечь config-only routes, commented sends и недостижимые handlers.

**Критерий:** S0 remote точно пуст по семантическому доказательству, а синтетический food case активирует правильную delivery chain с source=RabbitSend.send.

### Этап 6. Покрыть сценарно значимые оркестраторы в порядке отдачи

1. Общий путь search/detail/basic/seat/order; проверить `queryByBatch` и реальный SQL/пустые результаты.
2. Preserve G/D и Other с полным набором on/off опций; source summaries допустимы.
3. InsidePayment internal/external, new registration/auth, order state update.
4. Collect/execute; cancel quote/cancel и дешевый rebook; ранний refusal на collected.
5. Consign price/update, assurance lookup/types, food menus/store list.
6. S6 admin writes и visibility новой route/price/trip; all-order aggregation.
7. S5 cheapest/quickest/minStation/transfer и S7 date filters с фактическими date bugs.

**Критерий:** все указанные requests анализируются одним engine, state updates сохраняются, нет условий вида `if scenario_id == 'Sx': emit ...`.

### Этап 7. Стабилизация и сдача

- Relation-wise S0 reports и ablations; проверить grounding отдельно от traversal.
- Семантические invariants/metamorphic tests из следующего раздела.
- Просмотр всех unknown paths и крупных speculative remote/data clusters; исправлять причины, а не «обрезать до размера S0».
- Один JSON для S0–S7; deterministic sort/dedup; проверить формат и код выхода; приложить воспроизводимый код.
- После разрешённых leaderboard submits сохранять полный вариант и параметры: засчитывается **последняя принятая сдача**, не лучшая историческая. Не заменять устойчивый вариант случайным экспериментом перед дедлайном.

## 15. Локальная оценка без overfitting на S0

Один gold не позволяет получить честную оценку hidden generalization. Ни split 34 methods, ни случайное деление 24 calls не создаёт независимый validation set: edges принадлежат общим путям. В отчёте качества разделять измеренное S0 IoU, manually verified contracts и непроверенные hidden predictions.

Предлагаемая дисциплина:

1. **Зафиксировать стандартные baselines.** Отчёт TP/FP/FN по relation, source module, причине; сохранять версии и изменения. Не оптимизировать только общий score, который легко повышается пустым remote при пропущенном login.
2. **Grounding-only проверка.** S0 `entry` плюс ручная source-backed таблица endpoints для S1–S7. Последняя — не runtime gold и не основание объявить numerical accuracy всех edges, но позволяет ловить search→consign и register→contacts до traversal.
3. **Oracle-entry ablation.** Один и тот же engine с predicted requests и с известными S0 entry; дополнительно сравнить правильность request flags, которой entry set не проверяет. Это отделяет retrieval от execution modeling.
4. **Feature ablations.** Conditions, SQL state, DI, framework callbacks, enum resolution, ORM collections по отдельности. Проверять, что исчезает именно объяснённая группа FP/FN. Gold-only исправление password edge помечать annotation adjustment, не parser improvement.
5. **Metamorphic NL tests.** Перефразирование и перестановка необязательных слов не меняют request; «without food» и «no food» дают один DTO; Other/not high-speed не активирует high-speed search; admin-role переключается только в соответствующем шаге. Это проверка устойчивости к языку, не новый gold.
6. **Source-backed contrastive cases.** Verification absent/present; options on/off; existing/missing record; status PAID/COLLECTED; internal balance enough/empty; same consignee weight/changed weight. Проверять ожидаемое **изменение связей**, выведенное из конкретного if, а не полный придуманный trace.
7. **State invariants.** Коллекция после successful create непуста; collect изменяет status; отказ cancel не создаёт refund ledger row; admin route сохраняет stations/distances; разные scenarios начинают с одной snapshot. После create failed не утверждать успешный state update.
8. **Graph invariants.** Все IDs из nodes; directed triples; data-pair whitelist; abstract repository implementations не выданы как executions; common-call не remote; source/target каждого calls/remote присутствуют в executes. Это необходимые, не достаточные свойства.
9. **Вне-S0 проверки модели.** Написать маленькие искусственные Java snippets/IR fixtures для analyzer: Optional lazy supplier, enum receiver, short-circuit, multi-target URI, empty query, library callback, exception before catch. Запускать тесты **собственного analyzer**, не Train Ticket и не собирать его traces.
10. **Source review, не gold memorization.** Каждая summary привязана к method, source range, guards и effects. Правило нельзя обосновывать лишь тем, что «оно улучшило S0». Выделить review cases из S2/S4/S6/S7 до настройки эвристик.
11. **Аудит неопределённости.** Для каждого hidden scenario перечислять нерешённые inputs/semantics, число затронутых candidate edges и категории. Сначала проверять кандидатов с крупным FP blast radius. Не объявлять все unknowns success/failure одним глобальным правилом.

Не использовать `gold/S0.json` в production runtime как таблицу готовых outputs. Его разрешено использовать для обучения и проверок, но копирование правильных рёбер не строит программу по тексту любого сценария и не переносится на скрытые отличия.

## 16. Rust и бонус +10%

README требует, чтобы **анализ кода и построение ответа были реализованы на Rust**. Rust launcher вокруг Python baseline/интерпретатора бонуса не получает. Не закладывать в план тонкую обёртку или переписывание только JSON serializer.

Пусть полноценный вариант на другом языке даёт `P`, Rust-вариант — `R`. Rust выгоден при `min(1, 1.10R) > P`. До насыщения достаточно `R > P/1.10 ≈ 0.90909P`. То есть допустимая потеря **около 9,09% относительно P**, не десять абсолютных процентных пунктов. Например, при P=0,85 Rust должен дать больше 0,7727; вариант R=0,75 после бонуса даёт 0,825 и проигрывает. При R≥0,9091 достигается потолок 1,0.

Для данного корпуса bottleneck — семантика запросов/состояния/framework и качество условий, а не скорость обхода 15 тысяч nodes. Поэтому performance Rust само по себе мало помогает score. Python позволяет быстрее исследовать и править adapters; Rust потребует больше работы с dynamic DTO/state/unknown domains. В текущем PATH найден javac 11, но `cargo/rustc/mvn` через Get-Command не найдены; это **не доказательство отсутствия на диске**, но toolchain/setup ещё надо проверить. В этом исследовании ничего не устанавливалось и не компилировалось.

Rust оправдан, если команда уверенно пишет на нём, toolchain/парсер доступны, и архитектура B с ограниченными source-derived summaries реализуется в оставшееся время без потери критичных механизмов. Тогда именно Rust engine должен читать source, строить IR/conditions, интерпретировать state и формировать ответ; исследовательские Python audit scripts не обязаны становиться production dependencies.

Если навыки/время ограничены, рекомендую сначала ставить **качество semantics выше бонуса**. Потеря одной правильной family-ветки, external-payment случая или S7 refusal легко перекроет надбавку. Разумная точка решения — до реализации state/IR engine после уточнения дедлайна и готовности Rust toolchain; этого контекста сейчас нет. Не начинать Python production solver с обещанием «потом легко портируем»: стоимость позднего переноса и regression реальна.

Итоговая рекомендация условна: **B — в любом случае; Rust — только при подтверждённой способности сохранить ≥90,91% качества альтернативы и закончить реализацию.** Если такой уверенности нет, более полный engine на удобном команде языке вероятнее принесёт больший итоговый score. Для максимизации score бонус стоит учитывать, но не ценой пропуска state/framework/call-site analysis.

## Приложение A. Полный S0: canonical IDs и направленные пары

Эта выписка механически сверена с `gold/S0.json` и skeleton nodes. M01–M34 — обозначения только этого документа; каждое из 34 значений является target одного `executes` от `scenario:S0`. В submission обозначения Mxx недопустимы.

| Обозначение | Точный method ID | Исходник / начало метода |
|---|---|---|
| M01 | `method:ts-auth-service:auth.controller.UserController#getToken(auth.dto.BasicAuthDto,org.springframework.http.HttpHeaders)` | [UserController.java:41](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/controller/UserController.java:41) |
| M02 | `method:ts-auth-service:auth.entity.User#getAuthorities()` | [User.java:41](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/entity/User.java:41) |
| M03 | `method:ts-auth-service:auth.entity.User#getPassword()` | [User.java:46](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/entity/User.java:46) |
| M04 | `method:ts-auth-service:auth.entity.User#getUsername()` | [User.java:51](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/entity/User.java:51) |
| M05 | `method:ts-auth-service:auth.entity.User#isAccountNonExpired()` | [User.java:56](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/entity/User.java:56) |
| M06 | `method:ts-auth-service:auth.entity.User#isAccountNonLocked()` | [User.java:61](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/entity/User.java:61) |
| M07 | `method:ts-auth-service:auth.entity.User#isCredentialsNonExpired()` | [User.java:66](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/entity/User.java:66) |
| M08 | `method:ts-auth-service:auth.entity.User#isEnabled()` | [User.java:71](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/entity/User.java:71) |
| M09 | `method:ts-auth-service:auth.security.UserDetailsServiceImpl#loadUserByUsername(java.lang.String)` | [UserDetailsServiceImpl.java:26](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/security/UserDetailsServiceImpl.java:26) |
| M10 | `method:ts-auth-service:auth.security.jwt.JWTProvider#createToken(auth.entity.User)` | [JWTProvider.java:29](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/security/jwt/JWTProvider.java:29) |
| M11 | `method:ts-auth-service:auth.service.impl.TokenServiceImpl#getServiceUrl(java.lang.String)` | [TokenServiceImpl.java:54](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/service/impl/TokenServiceImpl.java:54) |
| M12 | `method:ts-auth-service:auth.service.impl.TokenServiceImpl#getToken(auth.dto.BasicAuthDto,org.springframework.http.HttpHeaders)` | [TokenServiceImpl.java:58](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/service/impl/TokenServiceImpl.java:58) |
| M13 | `method:ts-common:edu.fudan.common.entity.OrderStatus#getCode()` | [OrderStatus.java:45](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-common/src/main/java/edu/fudan/common/entity/OrderStatus.java:45) |
| M14 | `method:ts-common:edu.fudan.common.entity.SeatClass#getCode()` | [SeatClass.java:53](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-common/src/main/java/edu/fudan/common/entity/SeatClass.java:53) |
| M15 | `method:ts-common:edu.fudan.common.security.jwt.JWTFilter#doFilterInternal(javax.servlet.http.HttpServletRequest,javax.servlet.http.HttpServletResponse,javax.servlet.FilterChain)` | [JWTFilter.java:19](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-common/src/main/java/edu/fudan/common/security/jwt/JWTFilter.java:19) |
| M16 | `method:ts-common:edu.fudan.common.util.StringUtils#Date2String(java.util.Date)` | [StringUtils.java:31](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-common/src/main/java/edu/fudan/common/util/StringUtils.java:31) |
| M17 | `method:ts-common:edu.fudan.common.util.StringUtils#String2Date(java.lang.String)` | [StringUtils.java:15](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-common/src/main/java/edu/fudan/common/util/StringUtils.java:15) |
| M18 | `method:ts-consign-service:consign.controller.ConsignController#findByAccountId(java.lang.String,org.springframework.http.HttpHeaders)` | [ConsignController.java:46](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-consign-service/src/main/java/consign/controller/ConsignController.java:46) |
| M19 | `method:ts-consign-service:consign.service.ConsignServiceImpl#queryByAccountId(java.util.UUID,org.springframework.http.HttpHeaders)` | [ConsignServiceImpl.java:118](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-consign-service/src/main/java/consign/service/ConsignServiceImpl.java:118) |
| M20 | `method:ts-contacts-service:contacts.controller.ContactsController#findContactsByAccountId(java.lang.String,?HttpHeaders)` | [ContactsController.java:72](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-contacts-service/src/main/java/contacts/controller/ContactsController.java:72) |
| M21 | `method:ts-contacts-service:contacts.service.ContactsServiceImpl#findContactsByAccountId(java.lang.String,org.springframework.http.HttpHeaders)` | [ContactsServiceImpl.java:41](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-contacts-service/src/main/java/contacts/service/ContactsServiceImpl.java:41) |
| M22 | `method:ts-order-other-service:other.controller.OrderOtherController#queryOrdersForRefresh(other.entity.QueryInfo,org.springframework.http.HttpHeaders)` | [OrderOtherController.java:69](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-other-service/src/main/java/other/controller/OrderOtherController.java:69) |
| M23 | `method:ts-order-other-service:other.entity.Order#<init>()` | [Order.java:70](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-other-service/src/main/java/other/entity/Order.java:70) |
| M24 | `method:ts-order-other-service:other.service.OrderOtherServiceImpl#queryOrders(other.entity.QueryInfo,java.lang.String,org.springframework.http.HttpHeaders)` | [OrderOtherServiceImpl.java:142](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-other-service/src/main/java/other/service/OrderOtherServiceImpl.java:142) |
| M25 | `method:ts-order-other-service:other.service.OrderOtherServiceImpl#queryOrdersForRefresh(other.entity.QueryInfo,java.lang.String,org.springframework.http.HttpHeaders)` | [OrderOtherServiceImpl.java:208](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-other-service/src/main/java/other/service/OrderOtherServiceImpl.java:208) |
| M26 | `method:ts-order-service:order.controller.OrderController#queryOrdersForRefresh(order.entity.OrderInfo,org.springframework.http.HttpHeaders)` | [OrderController.java:64](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/controller/OrderController.java:64) |
| M27 | `method:ts-order-service:order.entity.Order#<init>()` | [Order.java:75](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/entity/Order.java:75) |
| M28 | `method:ts-order-service:order.entity.OrderInfo#getLoginId()` | [OrderInfo.java:39](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/entity/OrderInfo.java:39) |
| M29 | `method:ts-order-service:order.entity.OrderInfo#getState()` | [OrderInfo.java:47](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/entity/OrderInfo.java:47) |
| M30 | `method:ts-order-service:order.entity.OrderInfo#isEnableBoughtDateQuery()` | [OrderInfo.java:89](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/entity/OrderInfo.java:89) |
| M31 | `method:ts-order-service:order.entity.OrderInfo#isEnableStateQuery()` | [OrderInfo.java:93](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/entity/OrderInfo.java:93) |
| M32 | `method:ts-order-service:order.entity.OrderInfo#isEnableTravelDateQuery()` | [OrderInfo.java:85](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/entity/OrderInfo.java:85) |
| M33 | `method:ts-order-service:order.service.OrderServiceImpl#queryOrders(order.entity.OrderInfo,java.lang.String,org.springframework.http.HttpHeaders)` | [OrderServiceImpl.java:125](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/service/OrderServiceImpl.java:125) |
| M34 | `method:ts-order-service:order.service.OrderServiceImpl#queryOrdersForRefresh(order.entity.OrderInfo,java.lang.String,org.springframework.http.HttpHeaders)` | [OrderServiceImpl.java:191](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-order-service/src/main/java/order/service/OrderServiceImpl.java:191) |

Все 24 `calls` (source → target), без обратных и framework-соседних рёбер:

| № | Source | Target |
|---|---|---|
| 1 | M01 | M12 |
| 2 | M10 | M04 |
| 3 | M12 | M03 |
| 4 | M12 | M10 |
| 5 | M12 | M11 |
| 6 | M18 | M19 |
| 7 | M20 | M21 |
| 8 | M22 | M25 |
| 9 | M23 | M13 |
| 10 | M23 | M14 |
| 11 | M23 | M16 |
| 12 | M25 | M24 |
| 13 | M26 | M28 |
| 14 | M26 | M34 |
| 15 | M27 | M13 |
| 16 | M27 | M14 |
| 17 | M27 | M16 |
| 18 | M33 | M17 |
| 19 | M33 | M28 |
| 20 | M33 | M29 |
| 21 | M33 | M30 |
| 22 | M33 | M31 |
| 23 | M33 | M32 |
| 24 | M34 | M33 |

Все семь `data`; одинаковая table при разных source остаётся разными рёбрами:

| Source | Точный table ID |
|---|---|
| M09 | `res:table:ts-auth-service.auth_user` |
| M12 | `res:table:ts-auth-service.auth_user` |
| M12 | `res:table:ts-auth-service.user_roles` |
| M19 | `res:table:ts-consign-mysql.consign_record` |
| M21 | `res:table:ts-contacts-service.contacts` |
| M24 | `res:table:ts-order-other-service.orders_other` |
| M33 | `res:table:ts-order-service.orders` |

`remote = ∅`. Таблиц и методов вне каталога skeleton в выписке нет.

## Приложение B. Навигация по главным источникам

Ссылки ниже относятся к локальной поставке. Другие упомянутые module/file paths в документе заданы от корня `targets/trainticket/`.

- [Условия соревнования](C:/Users/PC/Documents/GitHub/tt-challenge/README.md)
- [Справочник разметки](C:/Users/PC/Documents/GitHub/tt-challenge/REFERENCE.md)
- [Контекст сценариев](C:/Users/PC/Documents/GitHub/tt-challenge/context/README.md)
- [Открытый эталон](C:/Users/PC/Documents/GitHub/tt-challenge/gold/S0.json)
- [BM25 baseline](C:/Users/PC/Documents/GitHub/tt-challenge/platform/baseline_bm25.py)
- [Closure/Index](C:/Users/PC/Documents/GitHub/tt-challenge/platform/baseline_closure.py)
- [UI grounding baseline](C:/Users/PC/Documents/GitHub/tt-challenge/platform/baseline_nlp.py)
- [Evaluation](C:/Users/PC/Documents/GitHub/tt-challenge/platform/evaluate_golden.py)
- [UI order list](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-ui-dashboard/static/assets/js/client_order_list.js)
- [UI booking/contacts/food/options](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-ui-dashboard/static/assets/js/client_ticket_book.js)
- [JWTUtil и discrepancy S0](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-common/src/main/java/edu/fudan/common/security/jwt/JWTUtil.java)
- [Authentication callbacks config](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-auth-service/src/main/java/auth/config/WebSecurityConfig.java)
- [G/D preserve conditions](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java)
- [Other preserve conditions](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-preserve-other-service/src/main/java/preserveOther/service/PreserveOtherServiceImpl.java)
- [Payment/ledger](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-inside-payment-service/src/main/java/inside_payment/service/InsidePaymentServiceImpl.java)
- [Collect/execute fallback](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-execute-service/src/main/java/execute/serivce/ExecuteServiceImpl.java)
- [Cancel/refund](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-cancel-service/src/main/java/cancel/service/CancelServiceImpl.java)
- [Rebook/state/dynamic URL](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-rebook-service/src/main/java/rebook/service/RebookServiceImpl.java)
- [Food creation/delivery](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-food-service/src/main/java/foodsearch/service/FoodServiceImpl.java)
- [Delivery listener/SQL](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-delivery-service/src/main/java/delivery/mq/RabbitReceive.java)
- [Search batch versus parallel](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-travel-service/src/main/java/travel/service/TravelServiceImpl.java)
- [Seat query/selection](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-seat-service/src/main/java/seat/service/SeatServiceImpl.java)
- [Register/auth state](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-user-service/src/main/java/user/service/impl/UserServiceImpl.java)
- [Gateway routes](C:/Users/PC/Documents/GitHub/tt-challenge/targets/trainticket/ts-gateway-service/src/main/resources/application.yml)

## Next Steps

До просмотра этого анализа реализация solver не начинается. После согласования направления задачи в порядке приоритета:

1. **P0 — правила разметки:** уточнить JWTFilter/JWTUtil, password call attribution, reflected Order constructors и owner collection SQL; зафиксировать явную interim policy и приоритет dump/context. Не блокировать независимое исследование request semantics.
2. **P0 — язык и границы engine:** выбрать архитектуру B; проверить Rust toolchain/навыки/оставшееся время и применимость бонуса до написания production state/IR.
3. **P0 — API grounding:** каталог Spring/gateway/UI и typed request plan каждого шага S0–S7; исправить consign/search/register/rebook/assurance ambiguities, сохранить отрицания и actor/family.
4. **P0 — данные:** binary-safe SQL reader, entity/resource map, initial snapshot, user/order/contact identities и ledger; подтвердить 8691.00 и непустые query results.
5. **P0 — execution core:** call-site-sensitive IR, short-circuit/return/exception/lambda semantics, DI и enum resolution; убрать verify/lambda/Other-date FP по исходникам.
6. **P1 — framework/ORM:** callbacks login, filter и содержательные constructors/collection effects по выбранной policy; регрессионный diff всех 34/24/0/7 элементов S0.
7. **P1 — границы сервисов:** URI+verb+host resolution, response propagation, negative probes и food→delivery MQ; не включать commented email sends.
8. **P1 — stateful flows:** booking options, internal/external pay, collect/used, cancel/refund/cheaper rebook, refusal after collect, consignee update, admin writes/new trip и advanced search.
9. **P1 — проверка обобщения:** source-derived contrastive cases, metamorphic NL, state/graph invariants, ablations; отделять semantic improvements от S0-only annotation fixes.
10. **P2 — сборка сдачи:** deterministic JSON S0–S7, zero rejected IDs/relations, аудит unknown ветвей, reproducible code и при необходимости полноценный Rust implementation; никаких запусков Train Ticket или самоснятых traces.
