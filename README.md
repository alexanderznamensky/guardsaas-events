# GuardSaaS Events for Home Assistant

https://www.ironlogic.ru/il_new.nsf/htm/ru_guardsaas

![image](https://github.com/user-attachments/assets/af104b1b-82a1-4243-9a51-874eb3ea5735) ![image](https://github.com/user-attachments/assets/889eb6ff-1544-4517-94b3-34b9e89ebdad)

Кастомная интеграция Home Assistant для работы с GuardSaaS.

Интеграция получает последние события по выбранным объектам GuardSaaS, показывает баланс аккаунта, дату оплаты и позволяет открывать шлагбаумы/ворота через официальный API GuardSaaS.

## Возможности

- Авторизация в GuardSaaS через логин и пароль.
- Выбор одного или нескольких объектов при первичной настройке.
- Создание сенсора последнего события для каждого объекта.
- Индивидуальные настройки для каждого объекта:
  - включение/отключение опроса;
  - интервал обновления;
  - лимит получаемых событий.
- Сенсор баланса аккаунта.
- Сенсор даты оплаты.
- Атрибут `message` для даты оплаты с текстовым напоминанием.
- Кнопка открытия ворот/шлагбаума.
- Автоматическое сопоставление объекта GuardSaaS с контроллером через поле `controllers`.
- Открытие контроллера через официальный endpoint GuardSaaS.


## Сенсоры

### Последнее событие объекта

Для каждого выбранного объекта создаётся отдельный сенсор:

```text
sensor.guardsaas_...
```

Атрибуты:

- `Время`
- `Квартира/Помещение`
- `Статус`
- `Телефон`
- `Автомобиль`
- `limit`
- `scan_interval`
- `enabled`

### Баланс

```text
GuardSaaS - Баланс
```

Баланс хранится как числовое значение.

Дополнительные атрибуты:

- `raw_balance`
- `all_balances`
- `conversion`
- `str_value`
- `formatted_balance`

Пример:

```text
state: 1483.8
str_value: 1483.80
formatted_balance: 1483.80 RUB
```

### Дата оплаты

```text
GuardSaaS - Дата оплаты
```

Дата отображается в формате:

```text
DD.MM.YYYY
```

Атрибуты:

- `raw_date`
- `days_left`
- `message`

Пример:

```text
state: 25.08.2026
days_left: 118
message: Все в порядке! Оплачивать GuardSaaS - аккаунт нужно через 118 дней.
```

## Кнопки открытия

Для каждого выбранного объекта создаётся кнопка:

```text
button.guardsaas_open_...
```

При нажатии интеграция:

1. авторизуется в GuardSaaS;
2. получает список объектов;
3. берёт `controller_id` из поля `controllers`;
4. получает `token1` из `/equipment/controller/api/list`;
5. отправляет команду открытия:

```text
POST /equipment/controller/api/{controller_id}/open_door/{token1}
```

Если основной endpoint не сработал, используется резервный:

```text
/equipment/cmd_opendoor/{controller_id}/{token1}
```

## Установка

Скопируйте папку интеграции в:

```text
custom_components/guardsaas_events/
```

После копирования перезапустите Home Assistant.

## Настройка

1. Откройте Home Assistant.
2. Перейдите в:

```text
Настройки → Устройства и службы → Добавить интеграцию
```

3. Найдите `GuardSaaS Events`.
4. Укажите логин и пароль от GuardSaaS.
5. Выберите один или несколько объектов.
6. Для каждого объекта задайте параметры опроса.

## Обновление

При обновлении версии рекомендуется удалить кэш Python:

```text
custom_components/guardsaas_events/__pycache__
```

Затем полностью перезапустить Home Assistant.

## Требования

Интеграция использует:

```json
[
  "requests",
  "beautifulsoup4"
]
```

## Примечания

- Интеграция работает через веб-авторизацию GuardSaaS.
- Для открытия ворот/шлагбаумов используется официальный API GuardSaaS.
- Если кнопка открытия не работает, проверьте наличие поля `controllers` в ответе `/object/list/export` и доступность `token1` через `/equipment/controller/api/list`.
- Если GuardSaaS временно недоступен или есть проблемы с DNS, сенсоры могут показывать ошибку обновления.

