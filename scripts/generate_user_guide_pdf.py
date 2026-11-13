"""Generate the illustrated Russian doctor guide as a stable A4 PDF."""
from pathlib import Path
import shutil

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "instrukciya-vracha.pdf"
WEB_OUTPUT = ROOT / "web" / "assets" / "instrukciya-vracha.pdf"
ACCENT = colors.HexColor("#0E5A78")
ACCENT_SOFT = colors.HexColor("#EAF4F8")
TEXT = colors.HexColor("#17212B")
MUTED = colors.HexColor("#5B6878")
BORDER = colors.HexColor("#DCE3E9")
SURFACE = colors.HexColor("#F5F8FA")
OK = colors.HexColor("#1D7A4C")
OK_SOFT = colors.HexColor("#E6F4EC")
WARN = colors.HexColor("#8F5D00")
WARN_SOFT = colors.HexColor("#FCF1DC")

pdfmetrics.registerFont(TTFont("Guide", r"C:\Windows\Fonts\arial.ttf"))
pdfmetrics.registerFont(TTFont("Guide-Bold", r"C:\Windows\Fonts\arialbd.ttf"))

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="GuideBody", fontName="Guide", fontSize=10.2, leading=14.3, textColor=TEXT, spaceAfter=4))
styles.add(ParagraphStyle(name="GuideSmall", fontName="Guide", fontSize=8.5, leading=11.5, textColor=MUTED))
styles.add(ParagraphStyle(name="GuideH1", fontName="Guide-Bold", fontSize=25, leading=29, textColor=TEXT, spaceAfter=7))
styles.add(ParagraphStyle(name="GuideH2", fontName="Guide-Bold", fontSize=16, leading=20, textColor=TEXT, spaceAfter=7))
styles.add(ParagraphStyle(name="GuideH3", fontName="Guide-Bold", fontSize=11, leading=14, textColor=TEXT, spaceAfter=3))
styles.add(ParagraphStyle(name="GuideWhite", fontName="Guide-Bold", fontSize=8.5, leading=11, textColor=colors.white, alignment=TA_CENTER))
styles.add(ParagraphStyle(name="GuideCenter", fontName="Guide-Bold", fontSize=9, leading=12, textColor=ACCENT, alignment=TA_CENTER))


def P(text, style="GuideBody"):
    return Paragraph(text.replace('\u2014', '-').replace('\u2013', '-').replace('\u2011', '-'), styles[style])


class StepBadge(Flowable):
    def __init__(self, number):
        super().__init__(); self.number = str(number); self.width = 11*mm; self.height = 11*mm
    def draw(self):
        c = self.canv; c.setFillColor(ACCENT); c.circle(self.width/2, self.height/2, 5*mm, fill=1, stroke=0)
        c.setFillColor(colors.white); c.setFont("Guide-Bold", 11); c.drawCentredString(self.width/2, self.height/2-3.5, self.number)


class UiPicture(Flowable):
    def __init__(self, kind, width=78*mm, height=54*mm):
        super().__init__(); self.kind=kind; self.width=width; self.height=height
    def _button(self, c, x, y, w, label, primary=False, red=False):
        c.setFillColor(ACCENT if primary else (ACCENT_SOFT if red else colors.white)); c.setStrokeColor(ACCENT if primary or red else BORDER)
        c.roundRect(x,y,w,7*mm,2*mm,fill=1,stroke=1); c.setFillColor(colors.white if primary else ACCENT); c.setFont("Guide-Bold",7.2)
        size=min(7.2,(w-(9 if red else 4)*mm)/pdfmetrics.stringWidth(label,"Guide-Bold",1))
        c.setFont("Guide-Bold",size)
        if red: c.drawString(x+7*mm,y+2.35*mm,label.strip())
        else: c.drawCentredString(x+w/2,y+2.35*mm,label)
        if red: c.setFillColor(colors.HexColor("#C73A31")); c.circle(x+4*mm,y+3.5*mm,1.1*mm,fill=1,stroke=0)
    def _browser(self,c,title):
        c.setFillColor(colors.HexColor("#EEF2F6")); c.setStrokeColor(BORDER); c.roundRect(0,0,self.width,self.height,4*mm,fill=1,stroke=1)
        c.setFillColor(colors.white); c.roundRect(0,self.height-9*mm,self.width,9*mm,4*mm,fill=1,stroke=0); c.rect(0,self.height-9*mm,self.width,5*mm,fill=1,stroke=0)
        c.setFillColor(colors.HexColor("#B9C4CE"))
        for i in range(3): c.circle((5+i*4)*mm,self.height-4.7*mm,1*mm,fill=1,stroke=0)
        c.setFillColor(MUTED); c.setFont("Guide",7); c.drawString(19*mm,self.height-6*mm,title)
    def draw(self):
        c=self.canv; self._browser(c, {"patient":"Выбор пациента","mic":"Проверка микрофона","test":"Начало обследования","task":"Задание 1 из 14","review":"Проверка записей"}.get(self.kind,"Обследование"))
        x=6*mm; y=6*mm; w=self.width-12*mm; h=self.height-20*mm
        c.setFillColor(colors.white); c.setStrokeColor(BORDER); c.roundRect(x,y,w,h,3*mm,fill=1,stroke=1)
        if self.kind=="patient":
            c.setFillColor(MUTED); c.setFont("Guide-Bold",6.5); c.drawString(x+5*mm,y+h-7*mm,"ПАЦИЕНТ")
            self._button(c,x+w-31*mm,y+h-11*mm,26*mm,"Добавить",True)
            c.setFillColor(SURFACE); c.roundRect(x+5*mm,y+h-20*mm,w-10*mm,7*mm,1.5*mm,fill=1,stroke=0); c.setFillColor(MUTED); c.setFont("Guide",6.5); c.drawString(x+8*mm,y+h-17.5*mm,"Фамилия  Имя  Отчество")
            c.setFillColor(ACCENT_SOFT); c.roundRect(x+5*mm,y+4*mm,w-10*mm,9*mm,2*mm,fill=1,stroke=0); c.setFillColor(ACCENT); c.setFont("Guide-Bold",8); c.drawString(x+9*mm,y+8*mm,"Иванов Иван Иванович"); c.drawRightString(x+w-9*mm,y+8*mm,"Выбрать")
        elif self.kind=="mic":
            c.setFillColor(TEXT); c.setFont("Guide-Bold",8.5); c.drawString(x+5*mm,y+h-8*mm,"Сначала проверьте микрофон")
            self._button(c,x+5*mm,y+h-20*mm,30*mm,"  Проверить микрофон",False,True); self._button(c,x+38*mm,y+h-20*mm,28*mm,"Провести тест",False)
            c.setFillColor(colors.HexColor("#E6EBEF")); c.roundRect(x+5*mm,y+11*mm,w-10*mm,3*mm,1*mm,fill=1,stroke=0); c.setFillColor(OK); c.roundRect(x+5*mm,y+11*mm,(w-10*mm)*.68,3*mm,1*mm,fill=1,stroke=0)
            c.setFillColor(OK); c.setFont("Guide-Bold",7); c.drawString(x+5*mm,y+5*mm,"Микрофон принимает звук")
        elif self.kind=="test":
            c.setFillColor(OK); c.setFont("Guide-Bold",7); c.drawString(x+5*mm,y+h-7*mm,"Микрофон проверен")
            self._button(c,x+5*mm,y+h-18*mm,30*mm,"Проверить микрофон",False,True); self._button(c,x+38*mm,y+h-18*mm,28*mm,"Провести тест",True)
            c.setFillColor(SURFACE); c.roundRect(x+10*mm,y+4*mm,w-20*mm,11*mm,2*mm,fill=1,stroke=0); c.setFillColor(TEXT); c.setFont("Guide-Bold",7); c.drawString(x+14*mm,y+10.5*mm,"Настройки звука проверены"); c.setFont("Guide",6.5);c.drawString(x+14*mm,y+6*mm,"Нажмите «Начать обследование»")
        elif self.kind=="task":
            c.setFillColor(ACCENT); c.rect(x,y+h-2*mm,w/14,2*mm,fill=1,stroke=0); c.setFillColor(MUTED); c.setFont("Guide-Bold",6.5); c.drawString(x+5*mm,y+h-8*mm,"ЗАДАНИЕ 1 ИЗ 14")
            c.setFillColor(TEXT); c.setFont("Guide-Bold",11); c.drawString(x+5*mm,y+h-15*mm,"Долгая «а»")
            c.setFillColor(ACCENT_SOFT); c.roundRect(x+5*mm,y+10*mm,w-10*mm,8*mm,2*mm,fill=1,stroke=0); c.setFillColor(ACCENT); c.setFont("Guide-Bold",8); c.drawCentredString(x+w/2,y+13*mm,"Скажите: «А-а-а…»")
            self._button(c,x+5*mm,y+2*mm,25*mm,"Начать попытку",True)
            c.setStrokeColor(ACCENT); c.setLineWidth(.8)
            for i,hh in enumerate([2,6,10,5,12,7,4,9,3,7,2]): c.line(x+38*mm+i*2*mm,y+4*mm-hh/2,x+38*mm+i*2*mm,y+4*mm+hh/2)
        elif self.kind=="review":
            c.setFillColor(TEXT); c.setFont("Guide-Bold",9); c.drawString(x+5*mm,y+h-8*mm,"Прослушайте задания")
            for i,label in enumerate(["Долгая «а»","Голос вверх","Самый тихий голос"]):
                yy=y+h-(16+i*7)*mm; c.setStrokeColor(BORDER); c.line(x+5*mm,yy+5*mm,x+w-5*mm,yy+5*mm); c.setFillColor(ACCENT); c.circle(x+8*mm,yy+2*mm,2.2*mm,fill=1,stroke=0)
                c.setFillColor(colors.white); play=c.beginPath();play.moveTo(x+7.5*mm,yy+1*mm);play.lineTo(x+7.5*mm,yy+3*mm);play.lineTo(x+9*mm,yy+2*mm);play.close();c.drawPath(play,fill=1,stroke=0)
                c.setFillColor(TEXT); c.setFont("Guide",7); c.drawString(x+13*mm,yy,label); c.setFillColor(ACCENT); c.drawRightString(x+w-5*mm,yy,"Перезаписать")
            self._button(c,x+w-34*mm,y+2*mm,29*mm,"Отправить на анализ",True)


def page_header_footer(canvas, doc):
    canvas.saveState(); width,height=A4
    canvas.setStrokeColor(BORDER); canvas.line(18*mm,height-14*mm,width-18*mm,height-14*mm)
    canvas.setFillColor(ACCENT); canvas.setFont("Guide-Bold",8); canvas.drawString(18*mm,height-10.5*mm,"АНАЛИЗ ГОЛОСА")
    canvas.setFillColor(MUTED); canvas.setFont("Guide",7.5); canvas.drawRightString(width-18*mm,10*mm,f"Инструкция врачу  ·  {doc.page}")
    canvas.restoreState()


def step(number, title, body, picture):
    left = Table([[StepBadge(number), P(title,"GuideH2")]], colWidths=[13*mm, 78*mm], style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),2)]))
    copy = [left, Spacer(1,2*mm)] + [P(x) for x in body]
    table = Table([[copy, UiPicture(picture,height=(66 if picture=='review' else 54)*mm)]], colWidths=[91*mm,78*mm], style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(0,0),6*mm),('LEFTPADDING',(1,0),(1,0),0),('RIGHTPADDING',(1,0),(1,0),0)]))
    return KeepTogether([table])


def callout(text, color=ACCENT_SOFT, border=ACCENT):
    t=Table([[P(text)]], colWidths=[169*mm]); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),color),('BOX',(0,0),(-1,-1),.8,border),('LEFTPADDING',(0,0),(-1,-1),5*mm),('RIGHTPADDING',(0,0),(-1,-1),5*mm),('TOPPADDING',(0,0),(-1,-1),3.5*mm),('BOTTOMPADDING',(0,0),(-1,-1),3.5*mm)])); return t


def build():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc=SimpleDocTemplate(str(OUTPUT),pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=20*mm,bottomMargin=16*mm,title="Как провести обследование голоса",author="Анализ голоса")
    story=[]
    story += [Spacer(1,8*mm), P("ПАМЯТКА ВРАЧУ · ОБНОВЛЕНО 13.11.2026","GuideCenter"), Spacer(1,5*mm), P("Как провести<br/>обследование голоса","GuideH1"), P("Подготовка пациента, 14 заданий, резервная копия и отправка на анализ. Восстановление записи и действия при сбоях."), Spacer(1,7*mm)]
    story.append(callout("<b>Главное правило:</b> тихое помещение, один и тот же микрофон и расстояние около <b>30 см</b> от пациента до устройства на протяжении всего теста."))
    story += [Spacer(1,8*mm), P("Весь порядок","GuideH2")]
    flow=[]
    for label in ["1  Выбрать пациента","2  Проверить микрофон","3  Провести тестирование","4  Выполнить 14 заданий","5  Прослушать записи","6  Скачать ZIP и отправить"]:
        flow.append(Table([[P(label,"GuideCenter")]],colWidths=[52*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),ACCENT_SOFT),('BOX',(0,0),(-1,-1),.6,colors.HexColor('#B9D6E2')),('TOPPADDING',(0,0),(-1,-1),5*mm),('BOTTOMPADDING',(0,0),(-1,-1),5*mm)])))
    story.append(Table([flow[:3],flow[3:]],colWidths=[56*mm]*3,style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),2*mm),('RIGHTPADDING',(0,0),(-1,-1),2*mm),('TOPPADDING',(0,0),(-1,-1),2*mm),('BOTTOMPADDING',(0,0),(-1,-1),2*mm)])))
    story += [Spacer(1,8*mm), P("Перед началом","GuideH2")]
    prep=[["✓","Закройте Zoom, Teams, Telegram и другие программы, которые могут занимать микрофон."],["✓","Не используйте Bluetooth-гарнитуру. Лучше встроенный или проводной микрофон."],["✓","Отключите шумоподавление, автоматическое усиление и другие «улучшения» звука."],["✓","Посадите пациента спокойно, попросите держать голову прямо и не менять положение."]]
    story.append(Table([[P(str(i),"GuideCenter"),P(b)] for i,(_,b) in enumerate(prep,1)],colWidths=[10*mm,159*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.white,SURFACE]),('GRID',(0,0),(-1,-1),.35,BORDER),('TOPPADDING',(0,0),(-1,-1),3*mm),('BOTTOMPADDING',(0,0),(-1,-1),3*mm)])))
    story += [PageBreak(), Spacer(1,12*mm), step(1,"Подготовьте пациента и устройство",["Закройте программы, использующие микрофон. Уберите Bluetooth-гарнитуру. Установите устройство примерно в 30 см от пациента.","<b>Пациенту:</b> сидеть спокойно и выполнять задания обычным голосом, если на экране не сказано иначе."],"patient"), Spacer(1,8*mm), step(2,"Выберите пациента",["На экране <b>«Обследование»</b> найдите пациента в списке по ФИО и нажмите <b>«Выбрать»</b>. ФИО сохраняется в вашей учётной записи.","Для нового пациента нажмите <b>«Добавить пациента»</b>, заполните ФИО и нажмите <b>«Сохранить и выбрать»</b>. В старой карточке нажмите <b>«Указать ФИО»</b>. Прежние обследования сохранятся."],"patient"), Spacer(1,7*mm), callout("<b>Важно:</b> при полном совпадении ФИО выберите правильную карточку вручную. Не меняйте пациента, микрофон, громкость и положение устройства после начала записи.",WARN_SOFT,WARN)]
    story += [PageBreak(), Spacer(1,12*mm), step(3,"Проверьте микрофон",["Нажмите <b>«Проверить микрофон»</b>. Разрешите браузеру доступ и попросите пациента произнести несколько слов в течение четырёх секунд.","Продолжайте, когда появится: <b>«Микрофон принимает звук. Можно проводить тестирование»</b>."],"mic"), Spacer(1,7*mm), callout("<b>Проверка не пройдена?</b> Убедитесь, что выбран нужный микрофон, проверьте уровень входа и расстояние до пациента, затем повторите проверку.",WARN_SOFT,WARN), Spacer(1,9*mm), step(4,"Запустите тестирование",["После успешной проверки нажмите <b>«Провести тестирование»</b>.","Прочитайте памятку, отметьте подтверждение и нажмите <b>«Начать обследование»</b>. Появится рабочий режим с крупными инструкциями.","Если включена калибровка, соблюдайте тишину и дождитесь окончания тестовых сигналов. Начинайте задание по приглашению на экране."],"test")]
    story += [PageBreak(), Spacer(1,12*mm), step(5,"Проведите 14 заданий",["На каждом экране показаны <b>инструкция врачу</b> и текст <b>«Что сказать пациенту»</b>. Нажмите <b>«Начать попытку»</b>, дождитесь выполнения и нажмите <b>«Готово»</b>. Выполните указанное число попыток.","Не говорите одновременно с пациентом и следите, чтобы не появлялся индикатор <b>«ПЕРЕГРУЗ»</b>. При ошибке выберите <b>«Перезаписать задание»</b>."],"task"), Spacer(1,9*mm), step(6,"Прослушайте и отправьте",["Прослушайте записи. Неудачное задание можно перезаписать отдельно кнопкой <b>«Перезаписать»</b>.","Нажмите <b>«Отправить на анализ»</b>. В окне <b>«Скачать копию перед отправкой?»</b> можно скачать ZIP или WAV, затем подтвердить отправку.","Чтобы отправить позже, выберите <b>«Завершить без отправки»</b>. Запись останется в списке локальных обследований."],"review"), Spacer(1,8*mm), callout("<b>Готово:</b> сервер подтвердил сохранение, когда открылся результат. Он доступен в разделе <b>«Мои исследования»</b>.",OK_SOFT,OK)]
    story += [PageBreak(), P("Автосохранение и восстановление","GuideH1"), P("Во время обследования звук и состояние заданий сохраняются в этом браузере примерно каждые две секунды. При аварийном закрытии последние ещё не сохранённые фрагменты могут отсутствовать."), Spacer(1,4*mm), P("Где найти запись","GuideH2"), P("На странице «Обследование» откройте <b>«Неотправленные обследования и локальные копии»</b>. Копии доступны в том же браузере под той же учётной записью.")]
    states=[("Записывается","Незавершённую копию можно открыть и продолжить."),("Сохранено","Запись сохранена в браузере и доступна для отправки."),("Отправляется","Дождитесь ответа; после прерывания проверьте результат."),("Ошибка","Прочитайте сообщение и повторите сохранение или отправку."),("Отправлено","Сервер подтвердил результат; копия остаётся до ручного удаления.")]
    story.append(Table([[P(a,"GuideH3"),P(b)] for a,b in states],colWidths=[38*mm,131*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,0),(-1,-1),[SURFACE,colors.white]),('LINEBELOW',(0,0),(-1,-1),.4,BORDER),('TOPPADDING',(0,0),(-1,-1),3*mm),('BOTTOMPADDING',(0,0),(-1,-1),3*mm)])))
    story += [Spacer(1,6*mm), P("Продолжить незавершённую запись","GuideH2"), P("1. Откройте локальную копию кнопкой <b>«Открыть / прослушать»</b>, чтобы проверить сохранённую часть, или нажмите <b>«Продолжить запись»</b>."), P("2. Используйте тот же микрофон. Прерванную попытку запишите заново и завершите остальные задания."), P("Если микрофон или частота записи изменились, скачайте сохранённую часть и начните новое обследование. Прерванная калибровка запускается заново в новом обследовании."), Spacer(1,5*mm), callout("<b>После сбоя отправки:</b> восстановите интернет, нажмите <b>«Проверить / повторить отправку»</b>, проверьте пациента и подтвердите отправку. Если анализ уже завершён, откроется сохранённый результат. Повтор той же неизменённой локальной записи не создаёт дубль.")]
    story += [PageBreak(), P("Резервная копия и загрузка файлов","GuideH1"), P("<b>ZIP</b> сохраняет звук, калибровку и разметку заданий для восстановления. <b>WAV</b> содержит только звук. Скачивание перед анализом необязательно. ZIP готового исследования доступен в «Моих исследованиях» и на странице результата."), Spacer(1,5*mm), P("Загрузить обследование из файла","GuideH2"), P("1. На странице «Обследование» откройте <b>«Загрузить запись из файла»</b> и выберите ZIP или аудиофайл."), P("2. Для отдельного файла выберите задание в поле <b>«Задание для отдельного аудиофайла»</b>. Поддерживаются WAV, MP3, OGG, WebM, M4A, FLAC и AAC; доступность кодека зависит от браузера."), P("3. Прослушайте запись, проверьте задания и калибровку. Выберите или подтвердите пациента текущей учётной записи."), P("4. Нажмите <b>«Анализировать»</b> и подтвердите отправку. Показатели из архива не подменяют новый расчёт."), Spacer(1,4*mm), P("Добавить попытку или недостающие задания","GuideH2"), P("Откройте обследование ZIP, выберите задание, отметьте <b>«Добавить аудиофайл как новую попытку в текущее обследование»</b> и загрузите аудио. Отдельный файл без разметки всех заданий не считается полным обследованием."), P("Незавершённый ZIP можно прослушать и дополнить файлами недостающих заданий. Продолжение пошаговой записи с сохранённого места доступно из локальной копии в браузере."), Spacer(1,4*mm), callout("<b>Размеры файлов:</b> загрузка до 128 МиБ, длительность до 30 минут. Для отправки звук вместе с калибровкой должен занимать менее 49 МиБ."), Spacer(1,6*mm), P("Выход и общий компьютер","GuideH2"), P("Копии остаются в браузере для вашей учётной записи после выхода. Скачайте нужные ZIP, затем удалите копии по одной или кнопкой <b>«Удалить все мои локальные копии»</b>. Очистка данных браузера тоже удаляет локальные записи; независимая резервная копия хранится в скачанном файле.")]
    story += [PageBreak(), P("Если что-то пошло не так","GuideH1")]
    issues=[("Браузер не видит микрофон","Разрешите доступ к микрофону в настройках сайта и повторите проверку."),("Тихий сигнал или «ПЕРЕГРУЗ»","Проверьте микрофон, расстояние и уровень входа. Задание с перегрузом перезапишите."),("Случайно закрыли тест","Откройте тот же браузер и аккаунт, найдите локальную копию и нажмите «Продолжить запись»."),("Ошибка хранения или мало места","Скачайте ZIP или WAV. Освободите место или разрешите хранение для сайта, затем нажмите «Повторить локальное сохранение»."),("Нет интернета или потерян ответ","Восстановите интернет и используйте «Проверить / повторить отправку». Уже готовый результат откроется без дубля."),("Сессия истекла","Скачайте копию. В другой вкладке войдите в ту же учётную запись, вернитесь к записи и повторите отправку."),("В другой вкладке сменили аккаунт","Войдите в исходную учётную запись для отправки этой записи. Локальные копии аккаунтов отображаются отдельно."),("Пациент ошибся или был шум","Завершите попытку и выберите «Перезаписать задание». При проверке готовых записей используйте «Перезаписать».")]
    cells=[]
    for title,body in issues: cells.append([P(title,"GuideH3"),P(body,"GuideSmall")])
    rows=[]
    for i in range(0,len(cells),2): rows.append([cells[i],cells[i+1]])
    story.append(Table(rows,colWidths=[83*mm,83*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,-1),SURFACE),('GRID',(0,0),(-1,-1),.6,BORDER),('LEFTPADDING',(0,0),(-1,-1),5*mm),('RIGHTPADDING',(0,0),(-1,-1),5*mm),('TOPPADDING',(0,0),(-1,-1),4*mm),('BOTTOMPADDING',(0,0),(-1,-1),4*mm)])))
    story += [Spacer(1,6*mm), P("Проверка перед отправкой","GuideH2")]
    final=[["□","Все 14 заданий выполнены."],["□","Нет записей с посторонними разговорами или сильным шумом."],["□","Нет записей с индикатором «ПЕРЕГРУЗ»."],["□","Неудачные задания перезаписаны и прослушаны."],["□","Выбран правильный пациент."]]
    story.append(Table([[P(a,"GuideH3"),P(b)] for a,b in final],colWidths=[10*mm,159*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-2),.4,BORDER),('TOPPADDING',(0,0),(-1,-1),3*mm),('BOTTOMPADDING',(0,0),(-1,-1),3*mm)])))
    story += [Spacer(1,6*mm), callout("Система предназначена для исследовательских целей и не заменяет медицинскую диагностику.",SURFACE,BORDER)]
    doc.build(story,onFirstPage=page_header_footer,onLaterPages=page_header_footer)
    shutil.copyfile(OUTPUT, WEB_OUTPUT)


if __name__ == "__main__":
    build()
    print(OUTPUT)
