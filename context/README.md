# Контекст исполнения

То, что кроме текста сценария определяет прогон (поле `context` скелета):

* **initial_state** — Fresh databases of every service filled by the services' own InitData at start-up, then one warm-up pass of a generic flow (it leaves a few orders, contacts and consignments of the pre-registered passenger); the exact state before every scenario is context/db-after-warmup.sql.gz: it is restored before each scenario, so scenarios do not see each other's effects.
* **account** — Unless the scenario registers a new one, the passenger is the pre-registered user fdse_microservice / 111111 (internal account balance 10 000, one contact 'Contacts_One' in the contact book, no verification code at login).
* **date** — All trips are searched and booked for 1 October 2026; the system clock is 15 September 2026.
* **choices** — 'The first' trip, contact, store or dish is the first element in the order the system returns it; where a dish or store is not further specified, the first one is taken. These choices do not change the executed code: runs with the second trip, another contact or another dish gave the same methods, calls, remote calls and data accesses (only statements differed, by 1 line).
* **profile** — Default Spring profile of every service; gateway on port 18888 routes /api/v1/<service>/** by service name; MySQL 8, RabbitMQ, Nacos; no external network.

`db-after-warmup.sql.gz` — состояние всех баз перед каждым сценарием (mysqldump после стартовых данных и прогрева).
