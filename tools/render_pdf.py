"""Render concise, extractable Russian-language theory presentations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

if TYPE_CHECKING:
    from tools.course_manifest import Session


FONT_NAME = "DejaVuSans"
FONT_PATH = Path("/usr/share/fonts/TTF/DejaVuSans.ttf")
_PAGE_SIZE = landscape(A4)
_NAVY = HexColor("#164A6B")
_BLUE = HexColor("#075D9B")
_PALE_BLUE = HexColor("#EAF2F7")
_GOLD = HexColor("#E5A400")
_TEXT = HexColor("#17212B")

_OUTCOME_INFINITIVES = {
    "объясняет": "объяснить",
    "формулирует": "сформулировать",
    "различает": "различать",
    "выбирает": "выбрать",
    "применяет": "применить",
    "сопоставляет": "сопоставить",
    "определяет": "определить",
    "разграничивает": "разграничить",
    "составляет": "составить",
    "оценивает": "оценить",
    "формирует": "сформировать",
    "классифицирует": "классифицировать",
    "сравнивает": "сравнить",
    "выстраивает": "выстроить",
    "редактирует": "отредактировать",
    "организует": "организовать",
}


def _register_font() -> None:
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        if not FONT_PATH.is_file():
            raise RuntimeError(f"Не найден Unicode-шрифт для презентации: {FONT_PATH}")
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))


def _student_outcome(outcome: str) -> str:
    first_word, separator, remainder = outcome.partition(" ")
    infinitive = _OUTCOME_INFINITIVES.get(first_word.casefold(), first_word)
    return f"{infinitive}{separator}{remainder}".rstrip(".")


def _sources_for(session: Session) -> tuple[tuple[str, str], ...]:
    if "ЕСПД" in session.block:
        return (
            ("ГОСТ 19.201-78: техническое задание", "standards.ru"),
            ("Каталог стандартов Росстандарта", "standards.ru"),
        )
    if "документац" in session.block.casefold():
        return (
            ("Официальная документация Sphinx", "sphinx-doc.org"),
            ("Официальная документация MkDocs", "mkdocs.org"),
        )
    return (
        ("ISTQB Certified Tester Foundation Level", "istqb.org"),
        ("Документация pytest", "docs.pytest.org"),
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PresentationTitle",
            parent=base["Title"],
            fontName=FONT_NAME,
            fontSize=16,
            leading=21,
            textColor=colors.white,
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "subtitle": ParagraphStyle(
            "PresentationSubtitle",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=14,
            leading=20,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
        "heading": ParagraphStyle(
            "SlideHeading",
            parent=base["Heading1"],
            fontName=FONT_NAME,
            fontSize=24,
            leading=30,
            textColor=_NAVY,
            spaceAfter=14,
        ),
        "body": ParagraphStyle(
            "SlideBody",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=15,
            leading=22,
            textColor=_TEXT,
            spaceAfter=9,
        ),
        "small": ParagraphStyle(
            "SlideSmall",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=12,
            leading=17,
            textColor=_TEXT,
            spaceAfter=6,
        ),
        "table": ParagraphStyle(
            "SlideTable",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=13,
            leading=18,
            textColor=_TEXT,
        ),
    }


def _bullet(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(f"• {text}", style)


def _slide_title(title: str, styles: dict[str, ParagraphStyle]) -> list[object]:
    return [Paragraph(title, styles["heading"]), Spacer(1, 5 * mm)]


def _page_number(canvas, document) -> None:
    canvas.saveState()
    canvas.setFillColor(_NAVY)
    canvas.setFont(FONT_NAME, 9)
    canvas.drawString(18 * mm, 12 * mm, "Поддержка и тестирование программных модулей")
    canvas.drawRightString(_PAGE_SIZE[0] - 18 * mm, 12 * mm, f"{document.page}")
    canvas.restoreState()


def _title_page(canvas, document) -> None:
    canvas.saveState()
    canvas.setFillColor(_NAVY)
    canvas.rect(0, 0, _PAGE_SIZE[0], _PAGE_SIZE[1], fill=1, stroke=0)
    canvas.setFillColor(_GOLD)
    canvas.rect(0, 0, _PAGE_SIZE[0], 10 * mm, fill=1, stroke=0)
    canvas.restoreState()


def render_presentation(session: Session, destination: Path) -> None:
    """Write an eight-slide PDF presentation for one theory ``session``."""
    _register_font()
    destination.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    concepts = session.keywords
    first, second, third, fourth = concepts
    story: list[object] = [
        Spacer(1, 48 * mm),
        Paragraph("Презентация по теме", styles["subtitle"]),
        Spacer(1, 9 * mm),
        Paragraph(session.title, styles["title"]),
        Spacer(1, 10 * mm),
        Paragraph(f"Раздел: {session.block}", styles["subtitle"]),
        PageBreak(),
        *_slide_title("Учебный результат", styles),
        Paragraph("После занятия вы сможете:", styles["body"]),
        _bullet(_student_outcome(session.outcome) + ".", styles["body"]),
        Spacer(1, 7 * mm),
        Paragraph(
            "Проверяйте вывод на примерах: наблюдение должно подтверждать выбранный результат.",
            styles["body"],
        ),
        PageBreak(),
        *_slide_title("Ключевые понятия", styles),
        *[_bullet(concept.capitalize(), styles["body"]) for concept in concepts],
        PageBreak(),
        *_slide_title("Связь понятий", styles),
        Paragraph("Логика рассмотрения темы:", styles["body"]),
        Table(
            [[first], ["↓"], [second], ["↓"], [third], ["↓"], [fourth]],
            colWidths=[190 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                    ("FONTSIZE", (0, 0), (-1, -1), 15),
                    ("LEADING", (0, 0), (-1, -1), 20),
                    ("TEXTCOLOR", (0, 0), (-1, -1), _TEXT),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("BACKGROUND", (0, 0), (-1, 0), _PALE_BLUE),
                    ("BACKGROUND", (0, 2), (-1, 2), _PALE_BLUE),
                    ("BACKGROUND", (0, 4), (-1, 4), _PALE_BLUE),
                    ("BACKGROUND", (0, 6), (-1, 6), _PALE_BLUE),
                    ("BOX", (0, 0), (-1, -1), 0.5, _BLUE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.white),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        ),
        PageBreak(),
        *_slide_title("Сравнение ориентиров", styles),
        Table(
            [
                [Paragraph("На что смотреть", styles["table"]), Paragraph("Какой вопрос задать", styles["table"])],
                [Paragraph(first.capitalize(), styles["table"]), Paragraph(f"Как «{first}» связано с «{second}»?", styles["table"])],
                [Paragraph(third.capitalize(), styles["table"]), Paragraph(f"Каким наблюдением можно подтвердить «{third}»?", styles["table"])],
                [Paragraph(fourth.capitalize(), styles["table"]), Paragraph(f"Какой риск возникает без учёта «{fourth}»?", styles["table"])],
            ],
            colWidths=[85 * mm, 105 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                    ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), _PALE_BLUE),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            ),
        ),
        PageBreak(),
        *_slide_title("Пример рассуждения", styles),
        Paragraph(f"Ситуация: в учебном проекте нужно применить «{first}».", styles["body"]),
        _bullet(f"Сначала зафиксируйте контекст и значение «{second}».", styles["body"]),
        _bullet(f"Затем выберите наблюдение, связанное с «{third}».", styles["body"]),
        _bullet(f"Сделайте вывод: какое решение подтверждает «{fourth}»?", styles["body"]),
        PageBreak(),
        *_slide_title("Самопроверка", styles),
        _bullet(f"Могу ли я объяснить различие между «{first}» и «{second}»?", styles["body"]),
        _bullet(f"Как «{third}» помогает получить проверяемый результат?", styles["body"]),
        _bullet(f"Какой риск останется, если не учесть «{fourth}»?", styles["body"]),
        PageBreak(),
        *_slide_title("Источники для самостоятельного изучения", styles),
        *[
            _bullet(f"{label} — {address}", styles["body"])
            for label, address in _sources_for(session)
        ],
        Spacer(1, 10 * mm),
        Paragraph("Сопоставляйте источник с конкретным вопросом темы и фиксируйте вывод своими словами.", styles["body"]),
    ]
    document = SimpleDocTemplate(
        str(destination),
        pagesize=_PAGE_SIZE,
        rightMargin=28 * mm,
        leftMargin=28 * mm,
        topMargin=24 * mm,
        bottomMargin=24 * mm,
        title=session.title,
        author="МДК.01.02 Поддержка и тестирование программных модулей",
    )
    document.build(story, onFirstPage=_title_page, onLaterPages=_page_number)
