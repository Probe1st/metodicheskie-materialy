from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


class DatabaseDesignMaterialsTest(TestCase):
    def test_generator_creates_complete_2025_sieup_package(self) -> None:
        from tools.build_database_design_materials import LESSONS, generate

        with TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "database-course"
            generate(output)

            folders = sorted(path for path in output.iterdir() if path.is_dir())
            self.assertEqual(len(LESSONS), 21)
            self.assertEqual(len(folders), 21)

            for lesson, folder in zip(LESSONS, folders, strict=True):
                self.assertTrue(folder.name.startswith(f"{lesson.number:02d}-"))
                files = {path.name for path in folder.iterdir() if path.is_file()}
                self.assertTrue(
                    {
                        "prakticheskaya-rabota.html",
                        "prakticheskie-zadaniya.html",
                        "domashnee-zadanie.html",
                    }
                    <= files
                )
                if lesson.kind == "theory":
                    self.assertTrue(
                        {"metodicheskij-material.html", "prezentaciya.pdf"} <= files
                    )
                else:
                    self.assertNotIn("metodicheskij-material.html", files)
                    self.assertNotIn("prezentaciya.pdf", files)

            first_material = (
                folders[0] / "metodicheskij-material.html"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "АНПОО «Сургутский институт экономики, управления и права» (СИЭУиП)",
                first_material,
            )
            self.assertIn("ОП.08 «Основы проектирования баз данных»", first_material)
            self.assertIn("2025–2026 учебный год", first_material)
            self.assertIn("<pre><code>", first_material)
