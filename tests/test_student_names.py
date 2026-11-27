"""Unit tests for student name normalization, format warnings, and duplicate grouping."""

from __future__ import annotations

import pytest

from app.student_names import (
    find_fuzzy_duplicate_groups,
    normalize_student_name,
    student_match_key,
    student_sorted_tokens,
)


def test_empty_and_invalid_inputs_rejected() -> None:
    with pytest.raises(TypeError):
        normalize_student_name(None)  # type: ignore[arg-type]

    for empty in ("", "   ", "\t\n", "+++", "---", "12345", "..."):
        with pytest.raises(ValueError, match="cannot be empty|must contain letters"):
            normalize_student_name(empty)


def test_preserves_clean_two_word_surname_first_name_without_warnings() -> None:
    for clean_name in (
        "Колотович Олег",
        "Дудинов Кирилл",
        "Сорокин Олег",
        "Мельников Богдан",
        "Шахлина Дарья",
        "Бронников Антон",
        "Манько Еремей",
        "Мухетдинов Кирилл",
        "Чепак Савелий",
        "Гасанова Лейла",
        "Бобоназарзода Мухаммадсаид",
        "Иванов Иван",
        "Петров Петр",
    ):
        res = normalize_student_name(clean_name)
        assert res.display_name == clean_name
        assert res.warnings == ()
        assert res.is_paired is False


def test_case_normalization_and_unicode_nfc() -> None:
    assert normalize_student_name("колотович олег").display_name == "Колотович Олег"
    assert normalize_student_name("КОЛОТОВИЧ ОЛЕГ").display_name == "Колотович Олег"
    assert normalize_student_name("  Е\u0308лка Петрова  ").display_name == "Ёлка Петрова"
    assert normalize_student_name("  иванов   иван  ").display_name == "Иванов Иван"


def test_patronymics_and_patronymic_typos_dropped_with_warning() -> None:
    cases = (
        ("ДУДИНОВ КИРИЛЛ АЛЕКСЕЕВИЧ", "Дудинов Кирилл"),
        ("Дудинов Кирилл Алексеевич", "Дудинов Кирилл"),
        ("СОРОКИН ОЛЕГ КОНСТАНТИНОВИЧ", "Сорокин Олег"),
        ("Сорокин Олег Константинович", "Сорокин Олег"),
        ("Мельников Богдан Артемович", "Мельников Богдан"),
        ("Мельников Богдан Артеомвич", "Мельников Богдан"),
        ("Мельников Богдан Артёмович", "Мельников Богдан"),
        ("Мельников Богдан Артемовис", "Мельников Богдан"),
        ("Сидоров Петр Алексеевич", "Сидоров Петр"),
    )
    for raw, expected in cases:
        res = normalize_student_name(raw)
        assert res.display_name == expected
        assert "extra_words_removed" in res.warning_codes
        assert any("МОЖЕТ быть неверным" in w or "может быть неверным" in w for w in res.warnings)


def test_production_filename_tails_cleaned_with_warning() -> None:
    cases = (
        ("Шахлина Дарья0", "Шахлина Дарья"),
        ("Бронников Антон Анарович 1.drawio", "Бронников Антон"),
        ("Манько Еремей 1. manko.drawio", "Манько Еремей"),
        ("Мухетдинов Кирилл Андреевич 1.А. Практическая работа №", "Мухетдинов Кирилл"),
        ("Чепак Савелий Павлович 1. Чепак Рупо", "Чепак Савелий"),
        ("Чепак Савелий Павлович 1. Порты SCSI и IDE. Чепак РУПО", "Чепак Савелий"),
        ("Гасанова Лейла Хаял кызы 1. ИС 25 9", "Гасанова Лейла"),
        (
            "Бобоназарзода Мухаммадсаид Торихи 1. Основные характеристики процессоров.pdf Бобоназарзода Мухаммадсаид ИС 25 9",
            "Бобоназарзода Мухаммадсаид",
        ),
        ("+Иванов Иван 2.zip", "Иванов Иван"),
        ("Иванов Иван.pdf", "Иванов Иван"),
        ("Иванов Иван - Лабораторная работа 1", "Иванов Иван"),
    )
    for raw, expected in cases:
        res = normalize_student_name(raw)
        assert res.display_name == expected
        assert res.warnings, f"Expected warning for noisy input {raw!r}"


def test_double_surname_and_double_name_with_hyphens_are_normal() -> None:
    res1 = normalize_student_name("иванов-петров алексей")
    assert res1.display_name == "Иванов-Петров Алексей"
    assert res1.warnings == ()

    res2 = normalize_student_name("петрова анна-мария")
    assert res2.display_name == "Петрова Анна-Мария"
    assert res2.warnings == ()

    res3 = normalize_student_name("МАМИН-СИБИРЯК ДМИТРИЙ НАРКИСОВИЧ")
    assert res3.display_name == "Мамин-Сибиряк Дмитрий"
    assert "extra_words_removed" in res3.warning_codes

    res4 = normalize_student_name("иванов — петров   анна – мария")
    assert res4.display_name == "Иванов-Петров Анна-Мария"

    res5 = normalize_student_name("салтыков - щедрин михаил")
    assert res5.display_name == "Салтыков-Щедрин Михаил"

    res6 = normalize_student_name("ИВАНОВ АННА - МАРИЯ")
    assert res6.display_name == "Иванов Анна-Мария"

    res7 = normalize_student_name("Иванов Иван - ИС")
    assert res7.display_name == "Иванов Иван"
    assert "filename_tail_removed" in res7.warning_codes

    res8 = normalize_student_name("Иванов И.О.")
    assert res8.display_name == "Иванов И"
    assert "initial_detected" in res8.warning_codes


def test_reversed_name_order_normalized_and_warns() -> None:
    res1 = normalize_student_name("иван иванов")
    assert res1.display_name == "Иванов Иван"
    assert "swapped_order" in res1.warning_codes

    res2 = normalize_student_name("Олег Колотович")
    assert res2.display_name == "Колотович Олег"
    assert "swapped_order" in res2.warning_codes

    res3 = normalize_student_name("Иван Петрович Сидоров")
    assert res3.display_name == "Сидоров Иван"
    assert "swapped_order" in res3.warning_codes

    res4 = normalize_student_name("анна-мария петрова")
    assert res4.display_name == "Петрова Анна-Мария"
    assert "swapped_order" in res4.warning_codes

    # Do NOT false-swap unusual/non-first-name word like 'Ёлка Петрова'
    res5 = normalize_student_name("Ёлка Петрова")
    assert res5.display_name == "Ёлка Петрова"


def test_single_word_and_ambiguous_inputs_emit_warnings() -> None:
    res1 = normalize_student_name("тестер")
    assert res1.display_name == "Тестер"
    assert "single_word" in res1.warning_codes

    res2 = normalize_student_name("ivanov ivan")
    assert res2.display_name == "Ivanov Ivan"
    assert "non_cyrillic" in res2.warning_codes

    res3 = normalize_student_name("Студент-12")
    assert res3.display_name == "Студент-12"
    assert "digits_present" in res3.warning_codes


def test_paired_submissions_detected_and_parsed() -> None:
    res = normalize_student_name("Гилюк Даниил и Бондаренко Владислав")
    assert res.is_paired is True
    assert res.paired_parts == ("Гилюк Даниил", "Бондаренко Владислав")
    assert "paired_names" in res.warning_codes

    res_comma = normalize_student_name("Гилюк Даниил, Бондаренко Владислав")
    assert res_comma.is_paired is True
    assert res_comma.paired_parts == ("Гилюк Даниил", "Бондаренко Владислав")

    res_tail = normalize_student_name("Гилюк Даниил и Бондаренко Владислав 1.zip")
    assert res_tail.is_paired is True
    assert res_tail.paired_parts == ("Гилюк Даниил", "Бондаренко Владислав")

    # Single student with comma typo should not be marked paired
    res_single_comma = normalize_student_name("Иванов, Иван")
    assert res_single_comma.is_paired is False
    assert res_single_comma.display_name == "Иванов Иван"


def test_match_key_and_fuzzy_grouping() -> None:
    assert student_match_key("Артём Мельников") == student_match_key("артем мельников")
    assert student_match_key("Колотович Олег") == student_match_key("колотович олег")

    records = [
        ("id1", "Мельников Богдан"),
        ("id2", "Мельников Бгодан"),
        ("id3", "Петрова Анна"),
        ("id4", "Петров Андрей"),
        ("id5", "Колотович"),
        ("id6", "Колотович Олег"),
    ]
    groups = find_fuzzy_duplicate_groups(records)
    id_sets = [set(g.student_ids) for g in groups]
    assert {"id1", "id2"} in id_sets
    assert {"id5", "id6"} in id_sets
    # Distinct students Петрова Анна and Петров Андрей must NOT be grouped together
    assert not any({"id3", "id4"}.issubset(s) for s in id_sets)


def test_fuzzy_similarity_reversed_typo_initial_and_hyphen_partial() -> None:
    assert student_sorted_tokens("Иван Иван") != student_sorted_tokens("Иван")

    records = [
        ("a1", "Иванов Алексей"),
        ("a2", "Алексй Иванов"),
        ("b1", "Иванов Иван"),
        ("b2", "Иванов И"),
        ("c1", "Иванов-Петров Дмитрий"),
        ("c2", "Иванов Дмитрий"),
        ("d1", "Петрова Анна-Мария"),
        ("d2", "Петрова Анна"),
        ("e1", "Александр Петров"),
        ("e2", "Петрова Александра"),
    ]
    groups = find_fuzzy_duplicate_groups(records)
    id_sets = [set(g.student_ids) for g in groups]

    assert any({"a1", "a2"}.issubset(s) for s in id_sets)
    assert any({"b1", "b2"}.issubset(s) for s in id_sets)
    assert any({"c1", "c2"}.issubset(s) for s in id_sets)
    assert any({"d1", "d2"}.issubset(s) for s in id_sets)
    assert not any({"e1", "e2"}.issubset(s) for s in id_sets)
