from __future__ import annotations
import html, shutil, subprocess
from pathlib import Path

ROOT=Path('АНПОО_СИЭУиП_11_25_ОП.02_Архитектура_аппаратных_средств')
LESSONS=[
('01','История_развития_ЭВМ','История развития ЭВМ','theory','механические вычислители, реле, электронные лампы, транзисторы, интегральные схемы и хранимую программу'),
('02','Практика_история_вычислительных_машин','История вычислительных машин','practice','хронологию механических, электромеханических и электронных машин'),
('03','Системы_счисления','Системы счисления и перевод чисел','theory','позиционные системы счисления, вес разряда и перевод между двоичной, восьмеричной, десятичной и шестнадцатеричной системами'),
('04','Арифметика_и_коды_чисел','Арифметика, логика и коды чисел','theory','двоичную арифметику, переполнение, AND, OR, NOT, XOR, бит, байт и целые коды'),
('05','Представление_и_кодирование_данных','Представление и кодирование данных','theory','целые и вещественные числа, Unicode, пиксель, разрешение, глубину цвета и объём изображения'),
('06','Кодирование_мультимедиа_и_логика','Кодирование мультимедиа и логика','theory','дискретизацию, частоту, разрядность, кодеки, контейнеры, сжатие и базовые логические схемы'),
('07','Практика_логические_элементы','Практика: логические элементы','practice','таблицы истинности, логические выражения и схемы из AND, OR, NOT и XOR'),
('08','Микропроцессор_и_системный_блок','Микропроцессор и системный блок','theory','процессор, ОЗУ, материнскую плату, накопители, блок питания, платы расширения и периферию'),
('09','Практика_компоненты_компьютера','Практика: компоненты компьютера','practice','компоненты системного блока, маркировку, разъёмы и назначение устройств'),
('10','Производительность_процессоров','Производительность процессоров','theory','закон Мура, ядра, потоки, частоту, IPC, кэш, тепловые ограничения и профиль нагрузки'),
('11','Практика_характеристики_процессоров','Практика: характеристики процессоров','practice','сравнение характеристик процессоров для офисной, творческой и вычислительной нагрузки'),
('12','Память_типы_и_характеристики','Память: типы и характеристики','theory','регистры, кэш, ОЗУ, ПЗУ, накопители, энергозависимость, скорость, объём и назначение'),
('13','Модули_и_иерархия_памяти','Модули и иерархия памяти','theory','DIMM, SO-DIMM, каналы памяти, адресацию, Flash, SSD, виртуальную память и иерархию'),
('14','Практика_виды_памяти','Практика: виды памяти','practice','определение уровней памяти и анализ характеристик модулей и накопителей'),
('15','Шины_и_системные_ресурсы','Шины и системные ресурсы','theory','шины адреса, данных и управления, пропускную способность, задержку, прерывания и DMA'),
('16','Интерфейсы_и_системная_логика','Интерфейсы и системная логика','theory','USB Type-C, USB4, PCI Express, SATA, NVMe, сетевые интерфейсы, ATX и чипсет'),
('17','Практика_устройство_управления_и_шины','Практика: устройство управления и шины','practice','схему обмена CPU, ОЗУ и устройства, линии управления и расчёт пропускной способности'),
('18','Практика_ввод_вывод','Практика: ввод-вывод','practice','классификацию устройств ввода-вывода, выбор совместимого интерфейса и безопасную диагностику'),
('19','Архитектура_вычислительных_систем','Архитектура вычислительных систем','theory','SISD, SIMD, MISD, MIMD, потоки команд и данных, latency, throughput и ограничения бенчмарков'),
('20','Параллельные_вычислительные_системы','Параллельные вычислительные системы','theory','многозадачность, параллелизм, многопроцессорные и многомашинные системы, закон Амдала и энергосбережение'),
('21','Практика_итоговая_работа','Практика: итоговая работа','practice','выбор конфигурации, совместимость компонентов, периферию, производительность, энергию и безопасность')]
SOURCES='<li><a href="https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html">Intel SDM</a></li><li><a href="https://www.usb.org/documents">USB-IF Document Library</a></li><li><a href="https://pcisig.com/specifications">PCI-SIG Specifications</a></li><li><a href="https://www.jedec.org/standards-documents">JEDEC Standards</a></li><li><a href="https://nvmexpress.org/specifications/">NVM Express</a></li><li><a href="https://www.unicode.org/standard/standard.html">Unicode Standard</a></li><li><a href="https://www.computerhistory.org/timeline/computers/">Computer History Museum</a></li>'
CSS='''@page{size:Letter;margin:.35in}body{max-width:980px;margin:auto;padding:28px;font:17px/1.55 Arial,sans-serif;color:#18212b;background:#fff}h1{color:#0b3d64}h2{margin-top:1.6em;color:#0b5d74}nav{background:#e8f3f8;padding:12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #9ab;padding:8px;vertical-align:top}.note{background:#fff7df;padding:12px;border-left:5px solid #d99b00}.task{background:#eef8ee;padding:12px}.page{page-break-after:always;box-sizing:border-box;height:7.8in;padding:.35in}@media print{body{max-width:none;padding:0}.page{page-break-after:always}}'''
def page(title,body): return f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{CSS}</style></head><body><nav><a href="#result">Результат</a> · <a href="#work">Работа</a> · <a href="#check">Проверка</a> · <a href="#sources">Источники</a></nav><main><h1>{html.escape(title)}</h1>{body}</main></body></html>'
def rubric(): return '<h2 id="check">Критерии проверки</h2><table><tr><th>Критерий</th><th>Что должно быть видно</th></tr><tr><td>Точность</td><td>Термины и расчёты использованы без ошибок.</td></tr><tr><td>Обоснование</td><td>Каждый вывод связан с данными или правилом.</td></tr><tr><td>Полнота</td><td>Выполнены обязательные пункты; результат можно проверить.</td></tr></table><h2 id="sources">Источники</h2><p>Дата обращения: 09.09.2026.</p><ul>'+SOURCES+'</ul>'
def practical(title,focus,kind):
 body=f'<section id="result"><h2>Что ты получишь</h2><p>Ты применишь знания про {focus} и подготовишь проверяемый результат.</p></section><section><h2>Подготовься безопасно</h2><ul><li>Не разбирай включённый компьютер и не касайся внутренних компонентов.</li><li>Не меняй BIOS/UEFI и не устанавливай непроверенные программы.</li><li>Записывай только наблюдаемые характеристики и источник данных.</li></ul></section><section id="work" class="task"><h2>Практическая работа</h2><ol><li>Прочитай условие и выдели входные данные.</li><li>Составь таблицу: объект, характеристика, назначение, источник или расчёт.</li><li>Проверь совместимость либо вычисление обратным способом.</li><li>Сделай вывод в 4–6 предложениях.</li></ol><p><b>Результат:</b> таблица и краткое обоснование в PDF или текстовом документе.</p></section>'+rubric()
 return page(title,body)
def assignment(title,focus): return page(title,f'<section id="result"><h2>Практическое задание</h2><p>Ситуация: нужно объяснить решение человеку, который выбирает или обслуживает устройство.</p></section><section id="work" class="task"><ol><li>Используй {focus}.</li><li>Выбери два варианта решения и сравни их по трём критериям.</li><li>Отметь ограничение каждого варианта.</li><li>Сформулируй рекомендацию без слов «лучше вообще»; назови конкретную задачу.</li></ol><p><b>Повышенный уровень:</b> предложи изменение условия, при котором рекомендация изменится.</p></section>'+rubric())
def homework(title,focus): return page(title,f'<section id="result"><h2>Домашнее задание</h2><p>Закрепи {focus}, не копируя результат практической работы.</p></section><section id="work" class="task"><h2>Обязательная часть</h2><ol><li>Составь пять карточек «термин — объяснение — пример».</li><li>Реши две задачи с полным ходом рассуждения.</li><li>Подготовь один вопрос, который остался после изучения темы, и попробуй найти на него ответ по источнику.</li></ol><h2>Повышенный уровень</h2><p>Создай мини-инфографику или схему, показывающую связь минимум трёх понятий темы.</p><p><b>Сдача:</b> один PDF или документ с заголовками и ссылками.</p></section>'+rubric())
def method(title,focus):
 blocks=['Наблюдай: где в обычном устройстве встречается эта тема?','Модель: раздели объект на части и назови функцию каждой части.','Правило: проверяй вывод обратным действием, спецификацией или совместимостью.','Применение: сначала опиши задачу, затем выбирай характеристику.','Ошибка: не путай название разъёма, интерфейс, протокол и реальную скорость.','Связь: объясни, как изменение одного параметра влияет на другой.']
 body=f'<section id="result"><h2>Результат изучения</h2><p>После материала ты сможешь объяснить и применить {focus}.</p></section><section><h2>Ключевые понятия</h2><p>{html.escape(focus)}.</p></section>'
 for i,b in enumerate(blocks,1): body+=f'<section><h2>{i}. {b.split(":")[0]}</h2><p>{b} На примере темы рассмотри: {html.escape(focus)}. Не запоминай отдельное число без единицы измерения, версии стандарта и условий применения.</p><div class="note"><b>Проверь себя:</b> сформулируй один признак, по которому можно отличить понятия в этой теме.</div></section>'
 body+=f'<section id="work" class="task"><h2>Мини-задание</h2><p>Составь схему из шести терминов темы и проведи между ними стрелки с подписями «передаёт», «хранит», «ограничивает» или «проверяет». Объясни две стрелки письменно.</p></section>'+rubric()
 return page(title,body)
def slides(title,focus):
 items=['Тема и результат','Зачем это нужно','Ключевые термины','Структура объекта','Правило работы','Пример расчёта или выбора','Типичная ошибка','Проверка совместимости','Практический сценарий','Вопросы самопроверки','Мини-задание','Итог','Источники']
 return '<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>'+CSS+'</style></head><body>'+''.join(f'<section class="page"><h1>{html.escape(title)}</h1><h2>{x}</h2><p>{html.escape(focus)}.</p><p>Сначала назови условие, затем используй характеристику и проверь вывод.</p></section>' for x in items)+'</body></html>'
def main():
 if ROOT.exists(): shutil.rmtree(ROOT)
 slidesdir=Path('.build/slides'); slidesdir.mkdir(parents=True,exist_ok=True)
 for n,slug,title,kind,focus in LESSONS:
  d=ROOT/f'{n}_{slug}';d.mkdir(parents=True)
  (d/'prakticheskaya_rabota.html').write_text(practical(title,focus,kind),encoding='utf-8')
  (d/'prakticheskoe_zadanie.html').write_text(assignment(title,focus),encoding='utf-8')
  (d/'domashnee_zadanie.html').write_text(homework(title,focus),encoding='utf-8')
  if kind=='theory':
   (d/'metodicheskiy_material.html').write_text(method(title,focus),encoding='utf-8')
   slide=slidesdir/f'{n}_{slug}.html';slide.write_text(slides(title,focus),encoding='utf-8')
   subprocess.run(['google-chrome-stable','--headless','--no-sandbox','--disable-gpu',f'--print-to-pdf={d/"prezentaciya.pdf"}',slide.resolve().as_uri()],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 print('Создано',sum(1 for p in ROOT.rglob('*') if p.is_file()),'файлов')
if __name__=='__main__': main()
