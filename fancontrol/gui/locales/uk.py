"""Ukrainian."""

CATALOG: dict[str, str] = {
    # -- main window ----------------------------------------------------
    "Fan Control": "Керування вентиляторами",
    "Controls": "Вентилятори",
    "Curves": "Криві",
    "Temperatures": "Температури",
    "Fan speeds": "Оберти вентиляторів",
    "Main": "Головна",
    "Fan control on": "Керування увімкнене",
    "Fan control off": "Керування вимкнене",
    "When off, the fans go back to whatever the motherboard firmware does.":
        "Коли вимкнено, вентиляторами знову керує прошивка материнської плати.",
    "Add curve": "Додати криву",
    "Import from FanControl…": "Імпорт із FanControl…",
    "Import from FanControl": "Імпорт із FanControl",
    "Read a userConfig.json from FanControl on Windows":
        "Прочитати userConfig.json із FanControl для Windows",
    "Rescan hardware": "Пересканувати обладнання",
    "Settings…": "Налаштування…",
    "Settings": "Налаштування",
    "Quit": "Вийти",
    "Show window": "Показати вікно",
    "{count} outputs · {path}": "виходів: {count} · {path}",
    "No controllable fans were found.\n\nOn most desktop boards the super-I/O driver has to "
    "be loaded first. Run 'fanctl doctor' to see what is missing, then use Rescan hardware.":
        "Не знайдено жодного вентилятора, яким можна керувати.\n\nНа більшості настільних "
        "плат спершу треба завантажити драйвер super-I/O. Запустіть «fanctl doctor», щоб "
        "побачити, чого бракує, а тоді натисніть «Пересканувати обладнання».",
    "Saved": "Збережено",
    "failed": "не вдалося",
    "rescan failed": "не вдалося пересканувати",
    "Hardware re-enumerated": "Обладнання проскановано заново",
    "This configuration cannot be applied:": "Цю конфігурацію не можна застосувати:",
    "Rename sensor": "Перейменувати датчик",
    "Name (leave empty for the original):": "Назва (порожня — повернути початкову):",
    "Curve {number}": "Крива {number}",
    "“{name}” is still used by: {users}.\n\nPoint those at another curve first.":
        "«{name}» ще використовують: {users}.\n\nСпершу перемкніть їх на іншу криву.",
    "Remove the curve “{name}”?": "Видалити криву «{name}»?",

    # -- settings -------------------------------------------------------
    "How often the curves are evaluated.": "Як часто перераховуються криві.",
    "Every managed fan goes to 100% above this temperature.\n"
    "Set to 0 to switch the override off.":
        "Вище цієї температури всі керовані вентилятори йдуть на 100%.\n"
        "0 вимикає це правило.",
    "The speed used when a sensor a curve needs cannot be read.\n"
    "Leave this high: a fan running too fast is better than a hot chip.":
        "Швидкість, коли датчик, потрібний кривій, не читається.\n"
        "Тримайте її високою: зайвий шум кращий за гарячий чип.",
    "Hand the fans back to the firmware when the daemon stops":
        "Віддавати вентилятори прошивці, коли служба зупиняється",
    "Update every": "Оновлювати кожні",
    "Force full speed above": "Повна швидкість вище",
    "Speed when a sensor fails": "Швидкість, якщо датчик збоїть",

    # -- calibration ----------------------------------------------------
    "Calibrate": "Калібрувати",
    "This spins {name} all the way up and down to find out where it stops and starts "
    "turning.\n\nIt takes a few minutes and the fan will be noisy. Continue?":
        "Вентилятор «{name}» буде розкручено до максимуму й пригальмовано до нуля, щоб "
        "з'ясувати, де він зупиняється і де рушає.\n\nЦе займе кілька хвилин, і буде "
        "шумно. Продовжити?",
    "calibration failed": "калібрування не вдалося",
    "Calibrating {name}…": "Калібрування «{name}»…",
    "Calibrating {name} — now at {percent}%": "Калібрування «{name}» — зараз {percent}%",
    "{name} kept turning all the way down to 0%, so it has no minimum to speak of.":
        "«{name}» крутився навіть на 0%, тож мінімуму в нього фактично немає.",
    "  stops turning below {percent}%": "  зупиняється нижче {percent}%",
    "  starts turning at {percent}%": "  рушає з {percent}%",
    "Apply the suggested settings?": "Застосувати запропоновані налаштування?",
    "  minimum speed → {percent}%": "  мінімальна швидкість → {percent}%",
    "  start speed → {percent}%": "  швидкість старту → {percent}%",

    # -- import ---------------------------------------------------------
    "Import": "Імпорт",
    "Open a FanControl configuration": "Відкрити конфігурацію FanControl",
    "FanControl configuration (*.json);;All files (*)":
        "Конфігурація FanControl (*.json);;Усі файли (*)",
    "This file could not be read as a FanControl configuration.":
        "Цей файл не вдалося прочитати як конфігурацію FanControl.",
    "The imported configuration was rejected.": "Імпортовану конфігурацію відхилено.",
    "Imported. These fans were left switched off:":
        "Імпортовано. Ці вентилятори залишено вимкненими:",
    "Imported": "Імпортовано",
    "Found <b>{curves}</b> curves and <b>{fans}</b> fans in the file.":
        "У файлі знайдено кривих: <b>{curves}</b>, вентиляторів: <b>{fans}</b>.",
    "Windows names hardware differently from Linux, so every sensor and fan has to be "
    "pointed at the real thing on this machine. Anything left unassigned is imported but "
    "stays switched off.":
        "Windows називає обладнання інакше, ніж Linux, тож кожен датчик і вентилятор "
        "треба зіставити з реальним на цьому комп'ютері. Усе незіставлене імпортується, "
        "але лишається вимкненим.",
    "Add to the current configuration instead of replacing it":
        "Додати до поточної конфігурації, а не замінювати її",
    "Keeps the fans you have already set up. A fan the imported file also drives is "
    "replaced by the imported one.":
        "Уже налаштовані вентилятори лишаються. Вентилятор, яким керує й імпортований "
        "файл, буде замінено імпортованим.",
    "Matched automatically": "Зіставлено автоматично",
    "Needs a decision": "Потребує рішення",
    "Nothing on this machine matches": "На цьому комп'ютері відповідника немає",
    "— leave unassigned —": "— не зіставляти —",
    "{name}   ·  {score} match": "{name}   ·  збіг {score}",

    # -- fan card -------------------------------------------------------
    "Let this program drive this fan": "Дозволити програмі керувати цим вентилятором",
    "Which curve drives this fan": "Яка крива керує цим вентилятором",
    "Manual": "Вручну",
    "Edit the selected curve": "Змінити вибрану криву",
    "Limits, spin-up and response settings": "Межі, розкручування та реакція",
    "Measure where this fan stops and starts. Takes a few minutes and\n"
    "spins the fan up and down while it runs.":
        "Виміряти, де вентилятор зупиняється і рушає. Займає кілька хвилин,\n"
        "вентилятор тим часом розкручується й сповільнюється.",
    "{rpm} rpm": "{rpm} об/хв",
    "Cool enough: the firmware is running this fan.":
        "Досить холодно: цим вентилятором керує прошивка.",
    "Calibrating — the curve is standing down.": "Калібрування — крива тимчасово не діє.",
    "Reads 0 rpm while being driven — raise the minimum or the start speed.":
        "Показує 0 об/хв, хоча мав би крутитися — підніміть мінімальну швидкість "
        "або швидкість старту.",
    "No matching hardware on this machine.": "На цьому комп'ютері немає такого обладнання.",
    "Spinning up…": "Розкручується…",
    "Driven by hand — the curve is not in control.": "Керується вручну — крива не діє.",

    # -- fan settings ---------------------------------------------------
    "{name} — settings": "{name} — налаштування",
    "The fan never runs slower than this while it is running at all.\n"
    "Set it above the speed at which the fan stalls.":
        "Поки вентилятор крутиться, він не йде повільніше за це.\n"
        "Ставте вище за швидкість, на якій він зупиняється.",
    "Added to whatever the curve asks for.": "Додається до того, що просить крива.",
    "Let the fan stop completely": "Дозволити вентилятору повністю зупинятися",
    "When the curve asks for less than the stop point, switch the fan\n"
    "off instead of holding the minimum.":
        "Коли крива просить менше за точку зупинки, вимикати вентилятор,\n"
        "а не тримати мінімум.",
    "Below this the fan is switched off rather than run slowly.\n"
    "0 uses the minimum speed as the threshold. Run Calibrate to\n"
    "measure where this fan actually stops.":
        "Нижче цього вентилятор вимикається, а не крутиться повільно.\n"
        "0 — порогом буде мінімальна швидкість. Калібрування покаже,\n"
        "де цей вентилятор насправді зупиняється.",
    "A fan that has stopped needs more than its running minimum to start\n"
    "turning. Run Calibrate to measure this.":
        "Зупиненому вентилятору, щоб рушити, треба більше, ніж його робочий\n"
        "мінімум. Калібрування це виміряє.",
    "How fast the fan may speed up. 0 means instantly.":
        "Як швидко вентилятор може прискорюватися. 0 — миттєво.",
    "How fast the fan may slow down. 0 means instantly.":
        "Як швидко вентилятор може сповільнюватися. 0 — миттєво.",
    "The tachometer on the same header. Used to show the RPM and to warn\n"
    "when the fan stops while being driven.":
        "Тахометр на тому ж роз'ємі. Показує оберти й попереджає,\n"
        "коли вентилятор стоїть, хоча мав би крутитися.",
    "— none —": "— немає —",
    "never": "ніколи",
    "Below this temperature the firmware runs the fan instead - for a\n"
    "GPU that means its own curve, which can stop the fans at idle.\n"
    "Taken back 3 °C before it would be handed over again.":
        "Нижче цієї температури вентилятором керує прошивка — для\n"
        "відеокарти це її власна крива, що може зупиняти вентилятори в простої.\n"
        "Керування повертається на 3 °C вище порогу.",
    "Minimum speed": "Мінімальна швидкість",
    "Maximum speed": "Максимальна швидкість",
    "Offset": "Зсув",
    "Stop below": "Зупиняти нижче",
    "Start at": "Рушати з",
    "Start for": "Розкручувати протягом",
    "Speed up limit": "Межа прискорення",
    "Slow down limit": "Межа сповільнення",
    "Fan tachometer": "Тахометр",
    "Firmware runs it below": "Прошивка керує нижче",
    "…measured on": "…за датчиком",
    "Measured: never turned at any speed.": "Виміряно: не крутився на жодній швидкості.",
    "Measured: turns even at {percent}%, up to {rpm} rpm.":
        "Виміряно: крутиться навіть на {percent}%, до {rpm} об/хв.",
    "Measured: turns from {percent}% upwards, up to {rpm} rpm.":
        "Виміряно: крутиться від {percent}% і вище, до {rpm} об/хв.",

    # -- curve cards ----------------------------------------------------
    "Click to edit, right-click for more": "Клацніть, щоб змінити; права кнопка — інші дії",
    "Show the cards you hid, so you can bring them back":
        "Показати приховані картки, щоб їх можна було повернути",
    "Show hidden ({count})": "Показати приховані ({count})",
    "Hide": "Приховати",
    "Show again": "Показувати знову",
    "Rename…": "Перейменувати…",
    "Edit…": "Змінити…",
    "Remove…": "Видалити…",
    "Hidden. “Show hidden” in the section header brings it back.":
        "Приховано. Повернути можна кнопкою «Показати приховані» в заголовку розділу.",
    "Edit this curve": "Змінити цю криву",
    "Remove this curve": "Видалити цю криву",
    "no sensor chosen": "датчик не вибрано",
    "{function}: {names}": "{function}: {names}",
    "nothing yet": "поки нічого",
    "Follows {fan}": "Слідує за «{fan}»",
    "Fixed speed, no sensor": "Стала швидкість, без датчика",
    "holds {temperature} °C": "тримає {temperature} °C",
    "Drives: {fans}": "Керує: {fans}",
    "unavailable": "недоступно",
    "{id}\nDouble-click to rename, right-click to hide":
        "{id}\nДвічі клацніть, щоб перейменувати; права кнопка — приховати",

    # -- curve types ----------------------------------------------------
    "Graph": "Графік",
    "Points you place yourself, joined by straight lines. The one to reach for unless "
    "you need something else.":
        "Точки, які ви ставите самі, з'єднані прямими. Беріть її, якщо не потрібно "
        "чогось особливого.",
    "Fixed speed": "Стала швидкість",
    "One speed, always. Useful as an input to a mix curve, or for a pump.":
        "Завжди одна швидкість. Згодиться як вхід для змішаної кривої або для помпи.",
    "Linear": "Лінійна",
    "A straight ramp between two temperatures. The same as a two point graph, but "
    "easier to type in exactly.":
        "Пряма між двома температурами. Те саме, що графік із двох точок, але "
        "простіше ввести точно.",
    "Target temperature": "Цільова температура",
    "Speeds up while the sensor is above the target and slows down while it is below, "
    "instead of mapping temperature to speed directly.":
        "Прискорюється, поки датчик вище цілі, і сповільнюється, поки нижче, — "
        "замість прямої відповідності температури й швидкості.",
    "Trigger": "Перемикач",
    "Two speeds with a gap between the thresholds, so the fan does not oscillate "
    "around one temperature.":
        "Дві швидкості з проміжком між порогами, щоб вентилятор не смикався довкола "
        "однієї температури.",
    "Mix": "Змішана",
    "Combines other curves - usually the highest of a CPU and a GPU curve, so case "
    "fans follow whichever is hotter.":
        "Поєднує інші криві — зазвичай бере більшу з кривих процесора й відеокарти, "
        "щоб корпусні вентилятори йшли за гарячішим.",
    "Sync": "Синхронна",
    "Follows another fan, optionally offset or scaled. Good for keeping a second fan "
    "on the same radiator in step.":
        "Повторює інший вентилятор, за бажання зі зсувом чи множником. Добре, щоб "
        "другий вентилятор на тому ж радіаторі йшов у ногу.",
    "Highest of them": "Найбільша з них",
    "Lowest of them": "Найменша з них",
    "Average": "Середня",
    "Sum": "Сума",
    "First minus the rest": "Перша мінус решта",

    # -- curve editor ---------------------------------------------------
    "New curve": "Нова крива",
    "What kind of curve?": "Яка крива?",
    "{name} — {title} curve": "{name} — крива «{title}»",
    "Name": "Назва",
    "{sensor}  (not present)": "{sensor}  (відсутній)",
    "— choose a sensor —": "— виберіть датчик —",
    "— choose a fan —": "— виберіть вентилятор —",
    "Temperature source": "Джерело температури",
    "Graph starts at": "Графік від",
    "Graph ends at": "Графік до",
    "The temperature range the graph covers. A GPU curve usually\n"
    "needs more than a CPU one. It only affects the drawing.":
        "Діапазон температур на графіку. Кривій відеокарти зазвичай\n"
        "потрібно більше, ніж процесорній. Впливає лише на малюнок.",
    "Drag a point to move it, double-click the graph to add one, right-click a point "
    "to remove it.":
        "Перетягуйте точку, щоб її посунути; двічі клацніть на графіку, щоб додати; "
        "клацніть правою кнопкою по точці, щоб видалити.",
    "Double-click to add a point": "Двічі клацніть, щоб додати точку",
    "Speed": "Швидкість",
    "Ramp starts at": "Підйом від",
    "Ramp ends at": "Підйом до",
    "Speed at the start": "Швидкість на початку",
    "Speed at the end": "Швидкість у кінці",
    "Slowest": "Найповільніше",
    "Fastest": "Найшвидше",
    "Speeds up by": "Прискорення на",
    "Slows down by": "Сповільнення на",
    "Dead band": "Мертва зона",
    "A target curve has no fixed shape: its speed depends on how long the sensor has "
    "been above or below the target, not only on the current temperature.":
        "Цільова крива не має сталої форми: швидкість залежить від того, як довго "
        "датчик був вище чи нижче цілі, а не лише від поточної температури.",
    "Speed up above": "Прискорювати вище",
    "Slow down below": "Сповільнювати нижче",
    "Speed when idle": "Швидкість у спокої",
    "Speed when loaded": "Швидкість під навантаженням",
    "Between the two temperatures the fan keeps whatever speed it already had. The "
    "preview shows the rising edge.":
        "Між двома температурами вентилятор тримає ту швидкість, яку вже мав. "
        "Попередній перегляд показує підйом.",
    "Combine using": "Як поєднувати",
    "Inputs": "Входи",
    "A mix curve has no shape of its own — it follows whichever of its inputs the "
    "chosen function picks.":
        "Змішана крива не має власної форми — вона йде за тим входом, який вибирає "
        "функція.",
    "Follow": "Повторювати",
    "Scaled by": "Множник",
    "Offset by": "Зсув на",
    "This curve follows another fan, so it has no shape of its own.":
        "Ця крива повторює інший вентилятор, тож власної форми не має.",
    "Hysteresis rising": "Гістерезис при нагріванні",
    "Hysteresis falling": "Гістерезис при охолодженні",
    "How far the temperature must rise before the fan speeds up.\n"
    "Leave at 0 to react to a rise immediately.":
        "На скільки має зрости температура, перш ніж вентилятор прискориться.\n"
        "0 — реагувати на зростання одразу.",
    "How far it must fall before the fan slows down. Raise this if the\n"
    "fan keeps hunting up and down around one temperature.":
        "На скільки вона має впасти, перш ніж вентилятор сповільниться. Збільште,\n"
        "якщо вентилятор постійно гойдається довкола однієї температури.",
    "Response rising": "Реакція на нагрівання",
    "Response falling": "Реакція на охолодження",
    "Smooths a rising temperature over this many seconds. 0 is off.":
        "Згладжує зростання температури за стільки секунд. 0 — вимкнено.",
    "Smooths a falling temperature. Making this larger than the rising\n"
    "one is what keeps the fans from dropping the moment a load ends.":
        "Згладжує падіння температури. Якщо зробити більшим за нагрівання,\n"
        "вентилятори не скидатимуть оберти, щойно навантаження скінчиться.",
    "Ignore hysteresis past the ends of the curve":
        "Ігнорувати гістерезис за краями кривої",
    "Out past the first and last point the speed is flat anyway, so\n"
    "holding the reading back there only delays the fans.":
        "За першою й останньою точками швидкість однаково стала, тож\n"
        "стримувати там показ означає лише запізнювати вентилятори.",

    # -- start-up -------------------------------------------------------
    "Direct hardware access needs root. Run the daemon instead:\n\n"
    "    sudo systemctl enable --now fancontrold":
        "Прямий доступ до обладнання потребує root. Натомість запустіть службу:\n\n"
        "    sudo systemctl enable --now fancontrold",
}
