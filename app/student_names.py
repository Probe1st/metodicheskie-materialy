"""Student display name normalization, format warnings, and duplicate detection."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

# Known unambiguous first names (lowercase, 'ё' folded to 'е') used for detecting
# reversed "<Имя> <Фамилия>" inputs without false-swapping arbitrary words.
# Deliberately excludes words that frequently occur as surnames (e.g. Роман, Лев, Ким).
KNOWN_FIRST_NAMES: frozenset[str] = frozenset({
    "август", "аврора", "агата", "агния", "адам", "аделина", "адиль", "адриан",
    "азамат", "аида", "айдар", "айгуль", "аким", "аксения", "алан", "алана",
    "алевтина", "александр", "александра", "алексей", "алёна", "алена", "али",
    "алина", "алиса", "алла", "алмаз", "альберт", "альбина", "альмира",
    "амалия", "амина", "амир", "анастасия", "анатолий", "ангелина", "андрей",
    "анжела", "анжелика", "анис", "анна", "антон", "антонина", "анфиса",
    "арина", "арман", "арсен", "арсений", "артем", "артём", "артемий", "артур",
    "асель", "ася", "ахмед", "аяз", "богдан", "борис", "бронислав", "булат",
    "вадим", "валентин", "валентина", "валерий", "валерия", "варвара",
    "василий", "василиса", "венера", "вера", "вероника", "виктор", "виктория",
    "виолетта", "виталий", "виталия", "влад", "владимир", "владислав",
    "владислава", "владлен", "всеволод", "вячеслав", "гавриил", "галина",
    "геннадий", "георгий", "герман", "глеб", "гордей", "григорий", "гузель",
    "гюнель", "давид", "дамир", "даниил", "данил", "данила", "данияр",
    "дарина", "дарья", "дементий", "демид", "денис", "джамиля", "диана",
    "дина", "динара", "дмитрий", "добрыня", "евгений", "евгения", "евдокия",
    "егор", "екатерина", "елена", "елизавета", "емельян", "еремей", "ермолай",
    "есения", "ефим", "ефросинья", "жан", "жанна", "захар", "зинаида",
    "зифа", "злата", "зоя", "ибрагим", "иван", "иванна", "игнат", "игнатий",
    "игорь", "ильдар", "ильназ", "ильнур", "ильяс", "илья", "инга", "инесса",
    "инна", "иосиф", "ирада", "ирина", "ислам", "исмаил", "камал", "камила",
    "камилла", "камиль", "карина", "каролина", "кира", "кирилл", "клавдия",
    "клим", "константин", "кристина", "ксения", "кузьма", "лада", "лариса",
    "лаура", "лейла", "леонид", "леся", "лиана", "лидия", "лилия", "лина",
    "любовь", "людмила", "мадина", "майя", "макар", "максим", "максимилиан",
    "малик", "малика", "марат", "маргарита", "марианна", "марина", "мария",
    "марк", "марсель", "марта", "марфа", "матвей", "милана", "милена",
    "мирон", "мирослав", "мирослава", "михаил", "мурад", "мухаммад",
    "мухаммадсаид", "мухаммед", "надежда", "назар", "наиль", "настя",
    "наталия", "наталья", "нелли", "никита", "николай", "николь", "нина",
    "нона", "оксана", "олег", "олеся", "ольга", "павел", "пелагея", "петр",
    "пётр", "платон", "полина", "прохор", "рада", "радик", "рамиль", "рамис",
    "рафаэль", "рашид", "регина", "ренат", "рената", "римма", "ринат",
    "роберт", "родион", "роза", "ростислав", "руслан", "рустам", "сабина",
    "савелий", "савва", "саида", "самира", "самат", "светлана", "святослав",
    "севастьян", "семен", "семён", "серафим", "серафима", "сергей", "снежана",
    "софия", "софья", "спартак", "станислав", "станислава", "степан",
    "стефания", "таисия", "тамара", "тарас", "татьяна", "тигран", "тимофей",
    "тимур", "трофим", "ульяна", "фаина", "фарида", "федор", "фёдор",
    "феликс", "филипп", "харитон", "эвелина", "эдгар", "эдуард", "элеонора",
    "элина", "эльвира", "эльдар", "эльмира", "эмиль", "эмилия", "эмма",
    "юлиана", "юлия", "юрий", "яков", "яна", "яромир", "ярослав", "ярослава",
})

# Male first-name stems used to distinguish standard Russian patronymics
# (e.g. "Петрович", "Алексеевич") from surnames ending in "-ович/-евич" (e.g. "Колотович").
_PATRONYMIC_FIRST_NAMES: frozenset[str] = frozenset({
    "александр", "алексей", "анатолий", "андрей", "антон", "аркадий",
    "арсений", "артем", "артём", "артур", "богдан", "борис", "вадим",
    "валентин", "валерий", "василий", "виктор", "виталий", "владимир",
    "владислав", "вячеслав", "геннадий", "георгий", "глеб", "григорий",
    "давид", "даниил", "данил", "денис", "дмитрий", "евгений", "егор",
    "еремей", "иван", "игорь", "илья", "кирилл", "константин", "кузьма",
    "лев", "леонид", "макар", "максим", "матвей", "михаил", "никита",
    "николай", "олег", "павел", "петр", "пётр", "платон", "роман",
    "руслан", "рустам", "савелий", "семен", "семён", "сергей", "станислав",
    "степан", "тимофей", "тимур", "федор", "фёдор", "филипп", "эдуард",
    "юрий", "яков", "ярослав",
})

_SURNAME_SUFFIXES: tuple[str, ...] = (
    "овский", "евская", "овская", "евский", "ицкий", "ицкая",
    "енко", "ович", "евич", "ова", "ева", "ёва", "ина", "ская", "цкая",
    "ский", "цкий", "чук", "щук", "юк", "ук", "зода", "швили", "дзе",
    "оглу", "оглы", "ов", "ев", "ёв", "ин", "ян", "ко",
)

_DISTINCT_SHORT_NAME_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"анна", "инна"}),
    frozenset({"елена", "алена"}),
    frozenset({"иван", "илья"}),
    frozenset({"дарья", "марья"}),
    frozenset({"максим", "марк"}),
    frozenset({"юлия", "лилия"}),
    frozenset({"егор", "игорь"}),
    frozenset({"арина", "ирина"}),
    frozenset({"карина", "марина"}),
    frozenset({"алла", "анжела"}),
})

_KNOWN_FILE_EXTENSIONS = (
    "drawio", "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
    "odt", "ods", "odp", "zip", "rar", "7z", "tar", "gz", "tgz",
    "txt", "py", "sql", "json", "csv", "png", "jpg", "jpeg", "md", "html", "htm",
)

_EXT_TAIL_RE = re.compile(
    r"\.(?:" + "|".join(_KNOWN_FILE_EXTENSIONS) + r")(?:\b.*|$)",
    re.IGNORECASE,
)
_NUMBERED_DOT_TAIL_RE = re.compile(r"\s+\d+\s*\..*$")
_TRAILING_BRACKET_RE = re.compile(r"(?:\s*[\(\[]\s*\d+[\)\]]|\s*№\s*\d*)\s*$")
_DASH_NORMALIZE_RE = re.compile(r"[–—−]")
_GLUED_DIGITS_RE = re.compile(r"(?<=[^\W\d_])\d+|\d+(?=[^\W\d_])")
_PAIRED_SEP_RE = re.compile(r"\s+(?:и|&|\+|/)\s+|\s*[&+/]\s*", re.IGNORECASE)
_HAS_LATIN_RE = re.compile(r"[A-Za-z]")
_ALLOWED_TOKEN_CHARS_RE = re.compile(r"[^\w\-]", re.UNICODE)
_TAIL_WORK_KEYWORDS: frozenset[str] = frozenset({
    "работа", "лабораторная", "практическая", "задание", "тест", "вариант",
    "тема", "группа", "рупо", "финал", "итог", "копия", "v1", "v2",
})


@dataclass(frozen=True, slots=True)
class StudentNameResult:
    display_name: str
    warnings: tuple[str, ...]
    warning_codes: tuple[str, ...]
    was_modified: bool
    is_paired: bool
    paired_parts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FuzzyDuplicateGroup:
    student_ids: tuple[str, ...]
    suggested_name: str
    reason: str
    confidence: str  # "high" | "medium"


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().replace("ё", "е")


def student_match_key(name: str) -> str:
    """Return comparison key for student names (NFC, collapsed whitespace, casefold, ё->е)."""
    cleaned = " ".join(unicodedata.normalize("NFC", name).strip().split())
    return cleaned.casefold().replace("ё", "е")


def student_token_set_key(name: str) -> frozenset[str]:
    """Return folded token set for order-insensitive comparison."""
    return frozenset(student_match_key(name).split())


def student_sorted_tokens(name: str) -> tuple[str, ...]:
    """Return sorted folded tokens for order-insensitive multiset comparison."""
    return tuple(sorted(student_match_key(name).split()))


def _strip_control_chars(text: str) -> str:
    return "".join(" " if unicodedata.category(c) in ("Cc", "Cf") else c for c in text)


def _capitalize_hyphenated(word: str) -> str:
    parts = [p for p in word.split("-") if p]
    if not parts:
        return ""
    return "-".join(p[:1].upper() + p[1:].lower() for p in parts)


def _is_first_name(word: str) -> bool:
    folded = _fold(word)
    if "-" in folded:
        parts = [p for p in folded.split("-") if p]
        return bool(parts) and all(p in KNOWN_FIRST_NAMES for p in parts)
    return folded in KNOWN_FIRST_NAMES


def _has_surname_suffix(word: str) -> bool:
    folded = _fold(word)
    if "-" in folded:
        parts = [p for p in folded.split("-") if p]
        return any(_has_surname_suffix(p) for p in parts)
    if len(folded) < 4:
        return False
    return folded.endswith(_SURNAME_SUFFIXES)


def _looks_like_patronymic(word: str) -> bool:
    folded = _fold(word)
    if folded in ("оглы", "оглу", "кызы", "улы", "уулу"):
        return True
    if folded.endswith(("овна", "евна", "ична", "инична")):
        return len(folded) >= 6
    if folded.endswith(("ович", "евич", "ич")):
        for stem in _PATRONYMIC_FIRST_NAMES:
            prefix = stem[:-1] if stem.endswith(("й", "ь", "а", "я")) else stem
            if len(prefix) >= 3 and folded.startswith(prefix[: min(4, len(prefix))]):
                return True
    return False


def _looks_like_first_last(first_cand: str, second_cand: str) -> bool:
    if not _is_first_name(first_cand) or _is_first_name(second_cand):
        return False
    if _looks_like_patronymic(second_cand):
        return False
    return _has_surname_suffix(second_cand)


def _split_paired_candidates(text: str) -> tuple[str, ...] | None:
    # 1. Explicit conjunction / symbol separator: "Гилюк Даниил и Бондаренко Владислав", "A / B", "A + B"
    parts = [p.strip() for p in _PAIRED_SEP_RE.split(text) if p.strip()]
    if len(parts) >= 2:
        valid_parts = [p for p in parts if sum(1 for c in p if c.isalpha()) >= 2]
        if len(valid_parts) >= 2:
            # Guard against initials like "Иванов И и П" (single-letter tokens)
            word_parts = [re.findall(r"[^\W\d_]+", p, re.UNICODE) for p in valid_parts]
            if all(any(len(w) >= 2 for w in ws) for ws in word_parts):
                return tuple(valid_parts)

    # 2. Comma separator: "Гилюк Даниил, Бондаренко Владислав" vs "Иванов, Иван"
    if "," in text:
        comma_parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(comma_parts) >= 2:
            left_words = re.findall(r"[^\W\d_]+", comma_parts[0], re.UNICODE)
            right_words = re.findall(r"[^\W\d_]+", comma_parts[1], re.UNICODE)
            is_single_surname_comma_name = (
                len(comma_parts) == 2
                and len(left_words) == 1
                and len(right_words) == 1
                and _is_first_name(right_words[0])
                and not _is_first_name(left_words[0])
            )
            if (
                not is_single_surname_comma_name
                and len(left_words) >= 1
                and len(right_words) >= 1
                and any(len(w) >= 2 for w in left_words)
                and any(len(w) >= 2 for w in right_words)
            ):
                return tuple(comma_parts)

    return None


def _strip_leading_and_file_tails(raw_text: str) -> tuple[str, list[tuple[str, str]]]:
    warnings: list[tuple[str, str]] = []
    text = unicodedata.normalize("NFC", raw_text)
    text = _strip_control_chars(text).strip()

    # Strip leading '+' (legacy reviewed file marker)
    if text.startswith("+"):
        text = text.lstrip("+").strip()
        warnings.append(("filename_tail_removed", "Удалён лишний префикс «+». Формат может быть неверным."))

    # Normalize Unicode dashes to ASCII hyphen and collapse underscores to spaces
    text = _DASH_NORMALIZE_RE.sub("-", text)
    if "_" in text:
        text = text.replace("_", " ")
        warnings.append(("invalid_punctuation", "Символы подчёркивания заменены пробелами. Формат может быть неверным."))

    # Check if filename extension or numbered dot tail is present
    before_tail = text
    text = _NUMBERED_DOT_TAIL_RE.sub("", text)
    text = _EXT_TAIL_RE.sub("", text)
    text = _TRAILING_BRACKET_RE.sub("", text)
    if text != before_tail:
        warnings.append((
            "filename_tail_removed",
            "Удалён хвост имени файла или номер попытки. Проверьте результат — формат может быть неверным.",
        ))

    return text, warnings


def _clean_single_student_tokens(
    pre_cleaned_text: str, initial_warnings: list[tuple[str, str]]
) -> tuple[list[str], list[tuple[str, str]]]:
    warnings = list(initial_warnings)
    text = pre_cleaned_text

    # Strip bare trailing numbers after 2+ word prefix (e.g. "Иванов Иван 1", "Иванов Иван 2 ИС 25")
    collapsed = " ".join(text.split())
    m_num_after_two = re.match(r"^([^\s\d]+(?:\s+[^\s\d]+)+)\s+\d+(?:\s+.*)?$", collapsed)
    if m_num_after_two:
        text = m_num_after_two.group(1)
        if not any(code == "filename_tail_removed" for code, _ in warnings):
            warnings.append((
                "filename_tail_removed",
                "Удалён числовой хвост в конце строки. Проверьте результат — формат может быть неверным.",
            ))

    # Strip digits directly glued to letters (e.g. "Шахлина Дарья0")
    before_glued = text
    text = _GLUED_DIGITS_RE.sub("", text)
    if text != before_glued:
        warnings.append((
            "digits_removed",
            "Удалены цифры, приклеенные к имени. Проверьте результат — формат может быть неверным.",
        ))

    # Separate fused initials like "И.О." into "И. О." before tokenizing
    text = re.sub(r"\.(?=[^\W\d_])", ". ", text)

    # Split whitespace, keeping standalone '-' tokens temporarily to inspect context
    raw_tokens = text.split()
    tokens: list[str] = []
    idx = 0
    while idx < len(raw_tokens):
        tok = raw_tokens[idx]
        if tok == "-":
            if len(tokens) == 1 and idx + 1 < len(raw_tokens):
                # e.g. "Иванов - Петров Алексей" -> merge around hyphen
                next_tok = raw_tokens[idx + 1]
                if next_tok != "-":
                    tokens[-1] = f"{tokens[-1]}-{next_tok}"
                    idx += 2
                    continue
            if (
                len(tokens) == 2
                and idx + 2 == len(raw_tokens)
                and not any(c.isdigit() for c in raw_tokens[idx + 1])
                and _fold(raw_tokens[idx + 1].strip(".,;:")) not in _TAIL_WORK_KEYWORDS
                and not (
                    len(raw_tokens[idx + 1]) <= 4
                    and raw_tokens[idx + 1].isupper()
                    and not tokens[-1].isupper()
                )
            ):
                # e.g. "Иванов-Петров Анна - Мария" -> merge double first name around hyphen
                next_tok = raw_tokens[idx + 1]
                if next_tok != "-":
                    tokens[-1] = f"{tokens[-1]}-{next_tok}"
                    idx += 2
                    continue
            if len(tokens) >= 2:
                # e.g. "Иванов Иван - Лабораторная работа 1" -> strip tail after dash
                warnings.append((
                    "filename_tail_removed",
                    "Удалена часть после дефиса. Если у вас двойная фамилия или имя, пишите дефис без пробелов — формат может быть неверным.",
                ))
                break
        tokens.append(tok)
        idx += 1

    # Clean punctuation from each token, preserving internal hyphens
    cleaned_tokens: list[str] = []
    punctuation_stripped = False
    for tok in tokens:
        no_punct = _ALLOWED_TOKEN_CHARS_RE.sub("", tok)
        no_punct = re.sub(r"-{2,}", "-", no_punct).strip("-")
        if no_punct != tok and tok not in (",", "."):
            punctuation_stripped = True
        if no_punct:
            cleaned_tokens.append(no_punct)

    if punctuation_stripped and not any(
        code in ("invalid_punctuation", "filename_tail_removed", "digits_removed") for code, _ in warnings
    ):
        warnings.append((
            "invalid_punctuation",
            "Удалены лишние знаки препинания. Проверьте результат — формат может быть неверным.",
        ))

    return cleaned_tokens, warnings


def normalize_student_name(raw: str) -> StudentNameResult:
    """Normalize a student input string to '<Фамилия> <Имя>' in Title Case.

    Preserves internal hyphens for double surnames and double first names.
    Emits warnings whenever the input format might be wrong or required cleanup.
    """
    if not isinstance(raw, str):
        raise TypeError(f"display_name must be a string, got {type(raw).__name__}")

    stripped_raw = unicodedata.normalize("NFC", _strip_control_chars(raw)).strip()
    if not stripped_raw:
        raise ValueError("display_name cannot be empty")

    pre_cleaned, initial_warnings = _strip_leading_and_file_tails(stripped_raw)

    # Check paired submission AFTER stripping filename tails (e.g. "1. Порты SCSI и IDE")
    paired_candidates = _split_paired_candidates(pre_cleaned)
    if paired_candidates is not None:
        normalized_parts: list[str] = []
        for part in paired_candidates:
            try:
                part_res = normalize_student_name(part)
                if not part_res.is_paired:
                    normalized_parts.append(part_res.display_name)
                else:
                    normalized_parts.append(" ".join(_capitalize_hyphenated(w) for w in part.split()))
            except ValueError:
                pass
        display_fallback = normalized_parts[0] if normalized_parts else " ".join(
            _capitalize_hyphenated(w) for w in pre_cleaned.split()[:2]
        )
        return StudentNameResult(
            display_name=display_fallback,
            warnings=(
                "Похоже, указано несколько студентов (парная сдача). Укажите одного студента в формате «Фамилия Имя» — формат может быть неверным.",
            ),
            warning_codes=("paired_names",),
            was_modified=display_fallback != stripped_raw,
            is_paired=True,
            paired_parts=tuple(normalized_parts),
        )

    cleaned_tokens, warnings = _clean_single_student_tokens(pre_cleaned, initial_warnings)
    letter_tokens = [t for t in cleaned_tokens if any(c.isalpha() for c in t)]
    if not letter_tokens:
        raise ValueError("display_name must contain letters")

    # Check if remaining tokens still have embedded digits (e.g. "Студент-12" or "Студент 1")
    if any(any(c.isdigit() for c in t) for t in letter_tokens):
        warnings.append((
            "digits_present",
            "В имени присутствуют цифры. Формат может быть неверным — ожидается «Фамилия Имя».",
        ))

    # Capitalize each token (preserving hyphenated parts, and digits if any remain like "Студент-12")
    formatted_tokens = [_capitalize_hyphenated(tok) for tok in letter_tokens]

    # Reduce to <= 2 tokens ("<Фамилия> <Имя>") and check ordering
    final_tokens: list[str]
    if len(formatted_tokens) == 1:
        final_tokens = formatted_tokens
        warnings.append((
            "single_word",
            "Указано только одно слово. Укажите фамилию и имя через пробел — формат может быть неверным.",
        ))
    elif len(formatted_tokens) == 2:
        w1, w2 = formatted_tokens
        if _looks_like_first_last(w1, w2):
            final_tokens = [w2, w1]
            warnings.append((
                "swapped_order",
                f"Похоже, указано «Имя Фамилия». Приведено к формату «{w2} {w1}» — проверьте, формат может быть неверным.",
            ))
        elif _looks_like_patronymic(w2):
            final_tokens = [w1, w2]
            warnings.append((
                "possible_patronymic_only",
                "Второе слово похоже на отчество. Убедитесь, что указаны именно фамилия и имя — формат может быть неверным.",
            ))
        elif _is_first_name(w1) and _is_first_name(w2):
            final_tokens = [w1, w2]
            warnings.append((
                "possible_reversed_order",
                "Оба слова похожи на имена. Проверьте порядок «Фамилия Имя» — формат может быть неверным.",
            ))
        elif _is_first_name(w1) and not _is_first_name(w2) and not _has_surname_suffix(w1):
            final_tokens = [w1, w2]
            warnings.append((
                "possible_reversed_order",
                "Первое слово похоже на имя. Убедитесь, что сначала указана фамилия, затем имя — формат может быть неверным.",
            ))
        else:
            final_tokens = [w1, w2]
    else:
        # 3+ tokens: check "<Имя> <Отчество> <Фамилия>" first
        w1, w2, w3 = formatted_tokens[0], formatted_tokens[1], formatted_tokens[2]
        if (
            len(formatted_tokens) == 3
            and _looks_like_patronymic(w2)
            and not _looks_like_patronymic(w3)
            and (_is_first_name(w1) or _has_surname_suffix(w3))
        ):
            final_tokens = [w3, w1]
            warnings.append((
                "swapped_order",
                f"Обнаружен порядок «Имя Отчество Фамилия». Приведено к «{w3} {w1}» — формат может быть неверным.",
            ))
            warnings.append((
                "extra_words_removed",
                f"Отчество убрано: оставлено «{w3} {w1}». Формат может быть неверным.",
            ))
        else:
            if _looks_like_first_last(w1, w2):
                final_tokens = [w2, w1]
                warnings.append((
                    "swapped_order",
                    f"Порядок слов изменён на «{w2} {w1}» — формат может быть неверным.",
                ))
            else:
                final_tokens = [w1, w2]
            joined = " ".join(final_tokens)
            warnings.append((
                "extra_words_removed",
                (
                    f"Введено больше двух слов (лишние слова/отчество убраны, оставлено «{joined}»). "
                    "Если у вас двойная фамилия или двойное имя, пишите их через дефис — формат может быть неверным."
                ),
            ))

    display_name = " ".join(final_tokens)

    # Check for single-letter initials (e.g. "Иванов И")
    if len(final_tokens) >= 2 and any(len(t.replace("-", "")) == 1 for t in final_tokens):
        warnings.append((
            "initial_detected",
            "Обнаружен инициал вместо полного имени. Укажите полные фамилию и имя — формат может быть неверным.",
        ))

    # Check for Latin characters
    if _HAS_LATIN_RE.search(display_name):
        warnings.append((
            "non_cyrillic",
            "Обнаружены латинские буквы. Проверьте раскладку — формат может быть неверным.",
        ))

    # Deduplicate warning codes while preserving order
    seen_codes: set[str] = set()
    dedup_codes: list[str] = []
    dedup_msgs: list[str] = []
    for code, msg in warnings:
        if code not in seen_codes:
            seen_codes.add(code)
            dedup_codes.append(code)
            dedup_msgs.append(msg)

    return StudentNameResult(
        display_name=display_name,
        warnings=tuple(dedup_msgs),
        warning_codes=tuple(dedup_codes),
        was_modified=display_name != stripped_raw,
        is_paired=False,
        paired_parts=(),
    )


def choose_canonical_display_name(names: Sequence[str]) -> str:
    """Choose the cleanest canonical '<Фамилия> <Имя>' among normalized variants.

    Prefers variants containing 'ё' over 'е' when otherwise equivalent,
    already-clean 2-word inputs, and higher frequency.
    """
    candidates: list[str] = []
    clean_candidates: list[str] = []
    for raw in names:
        try:
            res = normalize_student_name(raw)
        except ValueError:
            continue
        if res.is_paired:
            continue
        candidates.append(res.display_name)
        if not res.warnings:
            clean_candidates.append(res.display_name)

    pool = clean_candidates or candidates
    if not pool:
        return unicodedata.normalize("NFC", names[0]).strip() if names else ""

    counts = Counter(pool)
    return max(
        counts.keys(),
        key=lambda val: (
            "ё" in val.lower(),
            len(val.split()) == 2,
            counts[val],
            val,
        ),
    )


def damerau_levenshtein(a: str, b: str) -> int:
    """Compute optimal string alignment (restricted Damerau-Levenshtein) distance."""
    la, lb = len(a), len(b)
    if a == b:
        return 0
    if la == 0:
        return lb
    if lb == 0:
        return la

    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        dp[i][0] = i
    for j in range(lb + 1):
        dp[0][j] = j

    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                dp[i][j] = min(dp[i][j], dp[i - 2][j - 2] + 1)

    return dp[la][lb]


def _is_gender_token_pair(a: str, b: str) -> bool:
    """Return True when two folded tokens form a male/female Russian surname or first-name pair."""
    if a == b:
        return False
    if a + "а" == b or b + "а" == a:
        return True
    if (a.endswith("ий") and b == a[:-1] + "я") or (b.endswith("ий") and a == b[:-1] + "я"):
        return True
    if (a.endswith(("ский", "цкий")) and b == a[:-2] + "ая") or (
        b.endswith(("ский", "цкий")) and a == b[:-2] + "ая"
    ):
        return True
    return False


def _opposite_gender_pair(w1_a: str, w2_a: str, w1_b: str, w2_b: str) -> bool:
    """Detect distinct-gender pairs like 'Петров Александр' vs 'Петрова Александра' or 'Иванов Евгений' vs 'Иванова Евгения'."""
    sur_a, sur_b = _fold(w1_a), _fold(w1_b)
    nam_a, nam_b = _fold(w2_a), _fold(w2_b)
    sur_diff_gender = (
        (sur_a.endswith(("ов", "ев", "ёв", "ин")) and sur_b == sur_a + "а")
        or (sur_b.endswith(("ов", "ев", "ёв", "ин")) and sur_a == sur_b + "а")
        or (sur_a.endswith(("ский", "цкий")) and sur_b == sur_a[:-2] + "ая")
        or (sur_b.endswith(("ский", "цкий")) and sur_a == sur_b[:-2] + "ая")
    )
    return sur_diff_gender and _is_gender_token_pair(nam_a, nam_b)


def _pair_fuzzy_similarity(sa: str, fa: str, sb: str, fb: str) -> tuple[bool, str, str]:
    """Compare two aligned (surname, first_name) pairs for typos, initials, or partial hyphens."""
    if _opposite_gender_pair(sa, fa, sb, fb) or (
        _is_gender_token_pair(sa, sb) and _is_gender_token_pair(fa, fb)
    ):
        return False, "", ""

    d_sur = damerau_levenshtein(sa, sb)
    d_fir = damerau_levenshtein(fa, fb)

    # Same surname, close first name or initial
    if d_sur == 0:
        if fa != fb and (
            (len(fa) == 1 and fb.startswith(fa)) or (len(fb) == 1 and fa.startswith(fb))
        ):
            return True, "initial_match", "medium"
        if (
            ("-" in fa or "-" in fb)
            and fa != fb
            and (fa in fb.split("-") or fb in fa.split("-"))
        ):
            return True, "hyphen_partial_match", "medium"
        if frozenset({fa, fb}) in _DISTINCT_SHORT_NAME_PAIRS:
            return False, "", ""
        if d_fir == 1 or (d_fir == 2 and min(len(fa), len(fb)) >= 6):
            return True, "first_name_typo", "high" if d_fir == 1 else "medium"

    # Same first name, close surname or hyphenated surname part
    if d_fir == 0:
        if (
            ("-" in sa or "-" in sb)
            and sa != sb
            and (sa in sb.split("-") or sb in sa.split("-"))
        ):
            return True, "hyphen_partial_match", "medium"
        if d_sur == 1 or (d_sur == 2 and min(len(sa), len(sb)) >= 5):
            return True, "surname_typo", "high" if d_sur == 1 else "medium"

    # Small typos in both (length >= 5)
    if d_sur == 1 and d_fir == 1 and min(len(sa), len(sb), len(fa), len(fb)) >= 5:
        return True, "both_tokens_typo", "medium"

    return False, "", ""


def _are_fuzzy_similar(norm_a: str, norm_b: str) -> tuple[bool, str, str]:
    """Check if two normalized names likely represent the same student with a typo/swap."""
    key_a = student_match_key(norm_a)
    key_b = student_match_key(norm_b)
    if key_a == key_b:
        return True, "exact_normalized", "high"

    toks_a = key_a.split()
    toks_b = key_b.split()

    # Reversed 2-word order
    if len(toks_a) == 2 and len(toks_b) == 2 and sorted(toks_a) == sorted(toks_b):
        return True, "reversed_order", "high"

    # Single-word name matching first token of 2-word name
    if len(toks_a) == 1 and len(toks_b) == 2 and toks_a[0] in toks_b:
        return True, "single_word_match", "medium"
    if len(toks_b) == 1 and len(toks_a) == 2 and toks_b[0] in toks_a:
        return True, "single_word_match", "medium"

    if len(toks_a) == 2 and len(toks_b) == 2:
        sa, fa = toks_a
        sb, fb = toks_b

        ok, reason, conf = _pair_fuzzy_similarity(sa, fa, sb, fb)
        if ok:
            return True, reason, conf

        # Check swapped order + typo (e.g. "Алексй Иванов" vs "Иванов Алексей")
        ok_rev, reason_rev, _ = _pair_fuzzy_similarity(sa, fa, fb, sb)
        if ok_rev:
            return True, f"reversed_{reason_rev}", "medium"

    return False, "", ""


def find_fuzzy_duplicate_groups(records: Sequence[tuple[str, str]]) -> list[FuzzyDuplicateGroup]:
    """Group candidate duplicate student records within a single group using union-find.

    `records` is a sequence of `(student_id, display_name)`.
    Only returns groups with >= 2 student IDs that are not purely exact-key duplicates
    OR includes exact + fuzzy connected components when useful.
    Here returns connected components of fuzzy-similar records (size >= 2).
    """
    parsed: list[tuple[str, str, bool]] = []
    for st_id, raw_name in records:
        try:
            res = normalize_student_name(raw_name)
            parsed.append((st_id, res.display_name, res.is_paired))
        except ValueError:
            parsed.append((st_id, raw_name.strip(), False))

    n = len(parsed)
    parent = list(range(n))
    reasons: dict[tuple[int, int], tuple[str, str]] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(n):
        if parsed[i][2]:
            continue
        for j in range(i + 1, n):
            if parsed[j][2]:
                continue
            ok, reason, conf = _are_fuzzy_similar(parsed[i][1], parsed[j][1])
            if ok:
                union(i, j)
                reasons[(i, j)] = (reason, conf)

    components: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        components[find(i)].append(i)

    groups: list[FuzzyDuplicateGroup] = []
    for idxs in components.values():
        if len(idxs) < 2:
            continue
        ids = tuple(parsed[i][0] for i in idxs)
        comp_names = [parsed[i][1] for i in idxs]
        comp_reasons = [
            reasons[(a, b)]
            for idx_pos, a in enumerate(idxs)
            for b in idxs[idx_pos + 1 :]
            if (a, b) in reasons
        ]
        # Skip pure exact-normalized-only groups? No, caller can inspect reason!
        best_reason = comp_reasons[0][0] if comp_reasons else "similar"
        best_conf = "high" if any(c == "high" for _, c in comp_reasons) else "medium"
        suggested = choose_canonical_display_name(comp_names)
        groups.append(
            FuzzyDuplicateGroup(
                student_ids=ids,
                suggested_name=suggested,
                reason=best_reason,
                confidence=best_conf,
            )
        )

    return groups
