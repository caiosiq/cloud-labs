from __future__ import annotations

import json
import unittest

from lemma_gateway.command_language import CommandSyntaxError, parse_console_line


class CommandLanguageTests(unittest.TestCase):
    def test_move_matches_cloud_labs_envelope(self) -> None:
        parsed = parse_console_line("move tag_13 -300 0 -90")
        self.assertEqual(parsed.kind, "command")
        self.assertEqual(
            parsed.command,
            {
                "action": "MOVE_COMPONENT",
                "target_ref": "tag_13",
                "parameters": {
                    "target_x": -300.0,
                    "target_y": 0.0,
                    "rotation": -90.0,
                },
            },
        )

    def test_quoted_component_name_is_one_argument(self) -> None:
        parsed = parse_console_line('move "Gripper Camera 1" 10 20 30')
        self.assertEqual(parsed.command["target_ref"], "Gripper Camera 1")

    def test_preset_commands(self) -> None:
        self.assertEqual(parse_console_line("simreset list").kind, "preset_list")
        loaded = parse_console_line("simreset polarizer_test")
        self.assertEqual(loaded.arguments["selector"], "polarizer_test")
        saved = parse_console_line("simsave trial_1 --overwrite")
        self.assertEqual(saved.kind, "preset_save")
        self.assertTrue(saved.arguments["overwrite"])
        shown = parse_console_line("simshow test_random")
        self.assertEqual(shown.kind, "preset_show")
        self.assertEqual(shown.arguments["selector"], "test_random")

        document = {
            "schema_version": 1,
            "kind": "cloud_labs_simulation_preset",
            "base": "default",
            "components": {
                "tag_13": {
                    "presence": "breadboard",
                    "pose": {"x": -300, "y": 0, "rotation": -90},
                }
            },
        }
        written = parse_console_line(
            "simwrite authored --overwrite " + json.dumps(document)
        )
        self.assertEqual(written.kind, "preset_write")
        self.assertEqual(written.arguments["document"], document)
        self.assertTrue(written.arguments["overwrite"])

    def test_simwrite_reports_json_location(self) -> None:
        with self.assertRaisesRegex(CommandSyntaxError, "line 1 column"):
            parse_console_line("simwrite bad {not-json}")

    def test_simulation_component_commands(self) -> None:
        defined = parse_console_line(
            'simcomponent define tag_23 '
            '{"name":"Paper lens","type":"OPTICAL_LENS",'
            '"parameters":{"focal_length_mm":175}}'
        )
        self.assertEqual(defined.kind, "component_define")
        self.assertEqual(defined.arguments["tag_id"], "tag_23")
        self.assertEqual(
            defined.arguments["definition"]["parameters"]["focal_length_mm"],
            175,
        )

        configured = parse_console_line(
            'simcomponent configure tag_23 '
            '{"parameters":{"filter":"longpass"}}'
        )
        self.assertEqual(configured.kind, "component_configure")

        inserted = parse_console_line(
            "simcomponent insert tag_23 -200 120 45"
        )
        self.assertEqual(inserted.kind, "component_insert")
        self.assertEqual(inserted.arguments["payload"]["x"], -200.0)

        stored = parse_console_line(
            "simcomponent insert tag_23 storage 1 0"
        )
        self.assertEqual(
            stored.arguments["payload"]["storage_slot"], {"i": 1, "j": 0}
        )
        self.assertEqual(parse_console_line("simcomponent list").kind, "component_list")
        self.assertEqual(
            parse_console_line("simcomponent nexttag").kind,
            "component_next_tag",
        )
        self.assertEqual(parse_console_line("simclear all").kind, "component_clear")

    def test_simulation_component_requires_safe_tag(self) -> None:
        with self.assertRaisesRegex(CommandSyntaxError, "tag_id"):
            parse_console_line(
                'simcomponent define lens/../../bad {"name":"bad"}'
            )
        with self.assertRaisesRegex(CommandSyntaxError, "tag_id"):
            parse_console_line(
                'simcomponent define tag_paper_lens_1 {"name":"bad"}'
            )

    def test_non_finite_numbers_are_rejected(self) -> None:
        with self.assertRaises(CommandSyntaxError):
            parse_console_line("move tag_13 nan 0 0")

    def test_unknown_command_is_rejected(self) -> None:
        with self.assertRaisesRegex(CommandSyntaxError, "Unknown command"):
            parse_console_line("launch-the-robot")


if __name__ == "__main__":
    unittest.main()
