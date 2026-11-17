import tempfile
import unittest
import wave
import json
import random
from datetime import datetime
from pathlib import Path

from hitmo.database import Database
from hitmo.services import MediaLibrary, PlaybackHistoryService, PlaylistService, ProgrammingService, TimelineService, media_category


def make_wav(path: Path, seconds: float = 0.15) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(8000 * seconds)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x00" * frames)
    return path


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "studio.sqlite3")
        self.library = MediaLibrary(self.db)
        self.playlist = PlaylistService(self.db)
        self.timeline = TimelineService(self.db)
        self.programming = ProgrammingService(self.db)
        self.history = PlaybackHistoryService(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_database_initializes_and_seeds_operator(self):
        rows = self.db.all("SELECT name,role FROM operators WHERE active=1")
        self.assertEqual([(r["name"], r["role"]) for r in rows], [("Operador padrão", "Administrador")])

    def test_playback_history_filters_by_period_material_category_status_and_paginates(self):
        rows = (
            ("Oferta", "2026-11-14 23:55:00", "completed", "Comercial", 41),
            ("Oferta reprise", "2026-11-15 00:10:00", "interrupted", "Comercial", 42),
            ("Vinheta", "2026-11-15 08:00:00", "completed", "Vinheta", 43),
        )
        for title, started_at, status, category, material_id in rows:
            self.db.execute(
                "INSERT INTO playback_history(media_id,material_id_snapshot,title,artist,path,category,operator,started_at,ended_at,status) "
                "VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                (material_id, title, "Artista", f"C:/Audio/{title}.wav", category, "Locutor", started_at, started_at, status),
            )

        result = self.history.query(
            start_date="2026-11-15", end_date="2026-11-15", material="Oferta",
            category="Comercial", status="interrupted", limit=1,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual((result["history"][0]["title"], result["history"][0]["material_id"]), ("Oferta reprise", 42))
        self.assertEqual(self.history.query(status="completed", limit=1, offset=1)["total"], 2)

    def test_playback_history_rejects_invalid_or_reversed_periods(self):
        with self.assertRaisesRegex(ValueError, "AAAA-MM-DD"):
            self.history.query(start_date="15/11/2026")
        with self.assertRaisesRegex(ValueError, "anterior ou igual"):
            self.history.query(start_date="2026-11-16", end_date="2026-11-15")

    def test_playback_history_separates_selected_and_discarded_occurrence_items(self):
        audio = make_wav(self.root / "audit-item.wav")
        self.library.import_paths([audio], category="Música")
        media_id = int(self.db.one("SELECT id FROM media") ["id"])
        block_id = self.programming.add_block("Snapshot do bloco", "Música", "12:00", [6])
        self.programming.add_block_item(block_id, media_id)
        occurrence_id = self.programming.dispatch_window(
            datetime(2026, 11, 15, 12, 0), operator="Operador da grade",
        )["dispatch_ids"][0]
        item_id = int(self.db.one("SELECT id FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))["id"])

        selected = self.history.query(material=str(media_id))["history"]
        self.assertEqual(len(selected), 1)
        self.assertEqual((selected[0]["record_type"], selected[0]["status"], selected[0]["occurrence_status"]),
                         ("occurrence_item", "planned", "planned"))
        self.assertEqual(selected[0]["selection_state"], "selected")
        selected_filter = self.history.query(material=str(media_id), status="selected")["history"]
        self.assertEqual(len(selected_filter), 1)
        self.assertEqual(selected[0]["operator"], "Operador da grade")
        self.programming.set_occurrence_item_status(item_id, "discarded", "Inserção excedente")
        discarded = self.history.query(material=str(media_id), status="discarded")["history"]
        self.assertEqual(len(discarded), 1)
        self.assertEqual((discarded[0]["record_type"], discarded[0]["block_name_snapshot"], discarded[0]["error_message"]),
                         ("occurrence_item", "Snapshot do bloco", "Inserção excedente"))
        self.assertEqual(discarded[0]["material_id"], media_id)
        self.db.execute("DELETE FROM media WHERE id=?", (media_id,))
        preserved = self.history.query(material=str(media_id), status="discarded")["history"]
        self.assertEqual(preserved[0]["material_id"], media_id)

    def test_audio_library_hides_catalog_text_materials(self):
        self.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", ("hitmo://material/audio-1", "Audio", "Música"))
        self.db.execute("INSERT INTO media(path,title,category,text_content) VALUES(?,?,?,?)", ("hitmo://text/text-1", "Texto", "Texto", "Ler no ar"))
        self.assertEqual([row["title"] for row in self.library.search()], ["Audio"])

    def test_import_scans_folder_and_skips_duplicates(self):
        folder = self.root / "Comerciais"
        first = make_wav(folder / "Marca - Oferta.mp3")
        make_wav(folder / "Outro.wav")
        added, total = self.library.import_paths([folder])
        self.assertEqual((added, total), (2, 2))
        added, total = self.library.import_paths([folder])
        self.assertEqual((added, total), (0, 2))
        self.assertEqual(self.db.one("SELECT category FROM media WHERE path=?", (str(first.resolve()),))["category"], "Comercial")

    def test_playlist_order_can_be_rearranged_and_consumed(self):
        files = [make_wav(self.root / f"{name}.wav") for name in ("a", "b", "c")]
        self.library.import_paths(files)
        ids = [int(r["id"]) for r in self.db.all("SELECT id FROM media ORDER BY title")]
        for media_id in ids:
            self.playlist.append(media_id)
        queue = self.playlist.items()
        ordered = [int(r["item_id"]) for r in queue]
        new_order = [ordered[2], ordered[0], ordered[1]]
        self.playlist.reorder(new_order)
        self.assertEqual([int(r["item_id"]) for r in self.playlist.items()], new_order)
        self.assertEqual(int(self.playlist.pop_next()["item_id"]), new_order[0])
        self.assertEqual(len(self.playlist.items()), 2)
        retained = self.db.one("SELECT status FROM playlist_items WHERE id=?", (new_order[0],))
        self.assertEqual(retained["status"], "loading")

    def test_program_block_creates_durable_ordered_occurrence(self):
        files = [make_wav(self.root / f"track-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        block_id = self.programming.add_block("Manhã", "Música", "12:30", [6])
        source_items = [self.programming.add_block_item(block_id, media_id) for media_id in media_ids]
        now = datetime(2026, 11, 15, 12, 30)
        occurrence_ids = self.programming.due(now)
        self.assertEqual(len(occurrence_ids), 1)
        self.assertEqual(self.programming.due(now), [])
        rows = self.db.all("SELECT * FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_ids[0],))
        self.assertEqual([int(row["source_item_id"]) for row in rows], source_items)
        self.programming.set_occurrence_item_status(int(rows[0]["id"]), "playing")
        self.programming.set_occurrence_item_status(int(rows[0]["id"]), "completed")
        self.assertEqual(int(self.programming.next_occurrence_item()["id"]), int(rows[1]["id"]))
        self.programming.remove_block_item(source_items[0])
        self.programming.remove_block(block_id)
        retained = self.db.all("SELECT title,path,status FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_ids[0],))
        self.assertEqual(len(retained), 2)
        self.assertTrue(all(Path(row["path"]).is_file() for row in retained))

    def test_next_block_discards_only_unstarted_overrun_items_and_preserves_history(self):
        files = [make_wav(self.root / f"transition-{index}.wav") for index in range(4)]
        self.library.import_paths(files, category="Música")
        media = self.db.all("SELECT id,title,path FROM media ORDER BY id")
        earlier = self.programming.add_block("Bloco 12h", "Música", "12:00", [6])
        previous_items = [self.programming.add_block_item(earlier, int(row["id"])) for row in media[:3]]
        old_occurrence = self.programming.due(datetime(2026, 11, 15, 12, 0))[0]
        self.programming.set_occurrence_item_status(
            int(self.db.one("SELECT id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position LIMIT 1", (old_occurrence,))["id"]),
            "playing",
        )
        history_id = self.db.execute(
            "INSERT INTO playback_history(media_id,title,path,category,status) VALUES(?,?,?,'Música','completed')",
            (int(media[0]["id"]), "Já foi ao ar", str(media[0]["path"])),
        )
        incoming = self.programming.add_block("Bloco 12h01", "Música", "12:01", [6])
        self.programming.add_block_item(incoming, int(media[3]["id"]))

        created = self.programming.due(datetime(2026, 11, 15, 12, 1))
        self.assertEqual(len(created), 1)
        old_items = self.db.all("SELECT source_item_id,status,error_message FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (old_occurrence,))
        self.assertEqual([row["source_item_id"] for row in old_items], previous_items)
        self.assertEqual([row["status"] for row in old_items], ["playing", "discarded", "discarded"])
        self.assertTrue(all("excedente" in row["error_message"] for row in old_items[1:]))
        new_item = self.db.one("SELECT status FROM program_occurrence_items WHERE occurrence_id=?", (created[0],))
        self.assertEqual(new_item["status"], "planned")
        history = self.db.one("SELECT title,status FROM playback_history WHERE id=?", (history_id,))
        self.assertEqual((history["title"], history["status"]), ("Já foi ao ar", "completed"))
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 1)), [])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences WHERE scheduled_for='2026-11-15 12:01'")["n"], 1)

    def test_fixed_commercial_transition_is_snapshotted_and_discards_excess_without_erasing_history(self):
        music_files = [make_wav(self.root / f"fixed-music-{index}.wav") for index in range(2)]
        commercial_file = make_wav(self.root / "fixed-commercial.wav")
        self.library.import_paths(music_files, category="Música")
        self.library.import_paths([commercial_file], category="Comercial")
        music_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media WHERE category='Música' ORDER BY id")]
        commercial_id = int(self.db.one("SELECT id FROM media WHERE category='Comercial'")["id"])

        music_block = self.programming.add_block("Bloco musical", "Música", "12:00", [6])
        for media_id in music_ids:
            self.programming.add_block_item(music_block, media_id)
        old_occurrence = self.programming.due(datetime(2026, 11, 15, 12, 0))[0]
        old_items = self.db.all("SELECT id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (old_occurrence,))
        self.programming.set_occurrence_item_status(int(old_items[0]["id"]), "playing")
        history_id = self.db.execute(
            "INSERT INTO playback_history(media_id,title,path,category,status) VALUES(?,?,?,?,?)",
            (music_ids[0], "Executada antes do fixo", str(music_files[0].resolve()), "Música", "completed"),
        )

        fixed_block = self.programming.add_block(
            "Entrada comercial fixa", "Comercial", "12:01", [6],
            transition_policy="fixed", fixed_fade_ms=250,
        )
        self.assertIsNone(self.programming.next_fixed_transition(datetime(2026, 11, 15, 12, 0, 59)))
        self.programming.add_block_item(fixed_block, commercial_id)
        next_fixed = self.programming.next_fixed_transition(datetime(2026, 11, 15, 12, 0, 59))
        self.assertEqual(next_fixed, {"at": datetime(2026, 11, 15, 12, 1), "block_id": fixed_block, "fade_ms": 250})

        created = self.programming.due(datetime(2026, 11, 15, 12, 1))
        incoming = self.db.one("SELECT transition_policy,fixed_fade_ms FROM program_occurrences WHERE id=?", (created[0],))
        self.assertEqual((incoming["transition_policy"], incoming["fixed_fade_ms"]), ("fixed", 250))
        old_statuses = self.db.all("SELECT status,error_message FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (old_occurrence,))
        self.assertEqual([row["status"] for row in old_statuses], ["playing", "discarded"])
        self.assertIn("Fixo", old_statuses[1]["error_message"])
        self.assertEqual(self.db.one("SELECT status FROM playback_history WHERE id=?", (history_id,))["status"], "completed")
        with self.assertRaisesRegex(ValueError, "bloco comercial"):
            self.programming.add_block("Fixo inválido", "Música", "13:00", [6], transition_policy="fixed")

    def test_same_minute_blocks_are_prepared_once_without_skipping_each_other(self):
        files = [make_wav(self.root / f"same-minute-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        for index, media_id in enumerate(media_ids):
            block_id = self.programming.add_block(f"Mesmo minuto {index}", "Música", "12:30", [6])
            self.programming.add_block_item(block_id, media_id)
        created = self.programming.due(datetime(2026, 11, 15, 12, 30))
        self.assertEqual(len(created), 2)
        states = self.db.all("SELECT status FROM program_occurrence_items ORDER BY occurrence_id")
        self.assertEqual([row["status"] for row in states], ["planned", "planned"])
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 30)), [])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 2)

    def test_pending_occurrence_survives_recovery_without_duplicate_dispatch(self):
        files = [make_wav(self.root / f"recovery-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        block_id = self.programming.add_block("Retomada", "Música", "12:30", [6])
        for media_id in media_ids:
            self.programming.add_block_item(block_id, media_id)
        occurrence_id = self.programming.due(datetime(2026, 11, 15, 12, 30))[0]
        first, second = self.db.all("SELECT id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_id,))
        self.programming.set_occurrence_item_status(int(first["id"]), "playing")

        ProgrammingService(Database(self.db.path)).recover_interrupted()

        statuses = self.db.all("SELECT status FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_id,))
        self.assertEqual([row["status"] for row in statuses], ["interrupted", "planned"])
        self.assertEqual(int(self.programming.next_occurrence_item()["id"]), int(second["id"]))
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 30)), [])

    def test_empty_block_keeps_existing_ignore_behavior(self):
        self.programming.add_block("Bloco sem inserções", "Música", "12:30", [6])
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 30)), [])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 0)

    def test_scheduler_dispatches_within_grace_once_and_records_delay(self):
        audio = make_wav(self.root / "scheduler-late.wav")
        self.library.import_paths([audio], category="Música")
        media_id = int(self.db.one("SELECT id FROM media") ["id"])
        block_id = self.programming.add_block("Atraso tolerado", "Música", "12:00", [6])
        self.programming.add_block_item(block_id, media_id)

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 20))
        self.assertEqual(len(result["dispatch_ids"]), 1)
        self.assertEqual(result["missed_ids"], [])
        occurrence = self.db.one("SELECT status,dispatch_delay_ms,dispatch_reason FROM program_occurrences")
        self.assertEqual((occurrence["status"], occurrence["dispatch_delay_ms"]), ("delayed", 20000))
        self.assertIn("janela", occurrence["dispatch_reason"])
        self.assertEqual(len(self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 20))["dispatch_ids"]), 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 1)

    def test_scheduler_marks_old_slot_missed_without_selecting_or_advancing_rotation(self):
        folder_id, _media_ids = self._music_folder_with_tracks(["Alpha", "Bravo"])
        block_id = self.programming.add_block("Atrasado", "Música", "12:00", [6])
        self.programming.add_block_folder(block_id, folder_id, "random")

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 31))
        self.assertEqual(result["dispatch_ids"], [])
        self.assertEqual(len(result["missed_ids"]), 1)
        occurrence = self.db.one("SELECT status,dispatch_delay_ms,dispatch_reason FROM program_occurrences")
        item = self.db.one("SELECT status,selected_at,error_message,media_id FROM program_occurrence_items")
        self.assertEqual(occurrence["status"], "missed")
        self.assertEqual(occurrence["dispatch_delay_ms"], 31000)
        self.assertEqual(item["status"], "ignored")
        self.assertEqual(item["selected_at"], "")
        self.assertIsNone(item["media_id"])
        self.assertIn("perdido", item["error_message"])
        self.assertEqual(self.programming.next_occurrence_item(), None)
        self.assertEqual(self.db.one("SELECT decision FROM scheduler_decisions")["decision"], "missed")
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM folder_rotation_state WHERE folder_id=?", (folder_id,))["n"], 0)

    def test_scheduler_first_start_audits_current_minute_without_replaying_earlier_slots(self):
        files = [make_wav(self.root / f"startup-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        earlier_block = self.programming.add_block("Horário anterior", "Música", "11:59", [6])
        current_block = self.programming.add_block("Horário vencido", "Música", "12:00", [6])
        self.programming.add_block_item(earlier_block, media_ids[0])
        self.programming.add_block_item(current_block, media_ids[1])

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 45))

        self.assertEqual(len(result["missed_ids"]), 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 1)
        occurrence = self.db.one("SELECT block_id,status,dispatch_delay_ms FROM program_occurrences")
        self.assertEqual((occurrence["block_id"], occurrence["status"], occurrence["dispatch_delay_ms"]),
                         (current_block, "missed", 45000))
        self.assertEqual(self.db.one("SELECT block_id FROM program_occurrences")["block_id"], current_block)

    def test_scheduler_marks_missed_timeline_event_and_sets_its_daily_marker(self):
        audio = make_wav(self.root / "timeline-missed.wav")
        self.library.import_paths([audio], category="Comercial")
        media_id = int(self.db.one("SELECT id FROM media")["id"])
        event_id = self.timeline.add("12:00", [6], "Comercial atrasado", media_id, "Comercial")
        self.db.set_setting("scheduler_last_checked_at", "2026-11-15T11:59:59")

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 1, 0))
        occurrence = self.db.one("SELECT status,dispatch_delay_ms FROM program_occurrences WHERE timeline_event_id=?", (event_id,))
        item = self.db.one(
            "SELECT status,error_message FROM program_occurrence_items WHERE occurrence_id=(SELECT id FROM program_occurrences WHERE timeline_event_id=?)",
            (event_id,),
        )
        event = self.db.one("SELECT last_fired FROM timeline_events WHERE id=?", (event_id,))
        self.assertEqual(len(result["missed_ids"]), 1)
        self.assertEqual((occurrence["status"], occurrence["dispatch_delay_ms"]), ("missed", 60000))
        self.assertEqual(item["status"], "ignored")
        self.assertIn("não será executado", item["error_message"])
        self.assertEqual(event["last_fired"], "2026-11-15 12:00")

    def test_scheduler_recovery_expires_old_pending_without_touching_current_occurrence(self):
        files = [make_wav(self.root / f"resume-{index}.wav") for index in range(3)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        old_block = self.programming.add_block("Pendente antigo", "Música", "12:00", [6])
        for media_id in media_ids[:2]:
            self.programming.add_block_item(old_block, media_id)
        old_occurrence = self.programming.due(datetime(2026, 11, 15, 12, 0))[0]
        current_item = int(self.db.one("SELECT id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position LIMIT 1", (old_occurrence,))["id"])
        self.programming.set_occurrence_item_status(current_item, "playing")
        newer_block = self.programming.add_block("Novo bloco", "Música", "12:01", [6])
        self.programming.add_block_item(newer_block, media_ids[2])
        self.db.set_setting("scheduler_last_checked_at", "2026-11-15T12:00:00")

        result = self.programming.dispatch_window(
            datetime(2026, 11, 15, 12, 1, 0), preserve_item_id=current_item, force_recovery=True,
        )
        old_statuses = self.db.all("SELECT status FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (old_occurrence,))
        self.assertEqual([row["status"] for row in old_statuses], ["playing", "ignored"])
        self.assertEqual(self.db.one("SELECT status FROM program_occurrences WHERE id=?", (old_occurrence,))["status"], "running")
        self.assertEqual(len(result["dispatch_ids"]), 1)
        self.assertEqual(self.db.one("SELECT title FROM program_occurrence_items WHERE occurrence_id=(SELECT id FROM program_occurrences WHERE block_id=?)", (newer_block,))["title"], "resume-2")

    def test_scheduler_resumes_existing_recent_occurrence_after_restart_once(self):
        audio = make_wav(self.root / "restart-pending.wav")
        self.library.import_paths([audio], category="Música")
        media_id = int(self.db.one("SELECT id FROM media")["id"])
        block_id = self.programming.add_block("Pendente recente", "Música", "12:00", [6])
        self.programming.add_block_item(block_id, media_id)
        occurrence_id = self.programming.due(datetime(2026, 11, 15, 12, 0))[0]
        self.db.set_setting("scheduler_last_checked_at", "2026-11-15T12:00:00")

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 20), force_recovery=True)

        self.assertEqual(result["dispatch_ids"], [occurrence_id])
        self.assertEqual(self.db.one("SELECT status FROM program_occurrences WHERE id=?", (occurrence_id,))["status"], "delayed")
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))["n"], 1)
        self.assertEqual(self.db.one("SELECT decision FROM scheduler_decisions WHERE decision='resumed_pending'")["decision"], "resumed_pending")

    def test_scheduler_does_not_resume_fixed_occurrence_past_its_exact_time_tolerance(self):
        audio = make_wav(self.root / "fixed-restart.wav")
        self.library.import_paths([audio], category="Comercial")
        media_id = int(self.db.one("SELECT id FROM media")["id"])
        block_id = self.programming.add_block("Fixo vencido", "Comercial", "12:00", [6], transition_policy="fixed")
        self.programming.add_block_item(block_id, media_id)
        occurrence_id = self.programming.due(datetime(2026, 11, 15, 12, 0))[0]
        self.db.set_setting("scheduler_last_checked_at", "2026-11-15T12:00:00")

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 5), force_recovery=True)

        self.assertEqual(result["dispatch_ids"], [])
        self.assertEqual(self.db.one("SELECT status FROM program_occurrences WHERE id=?", (occurrence_id,))["status"], "missed")
        self.assertEqual(self.db.one("SELECT status FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))["status"], "ignored")

    def test_scheduler_fixed_conflict_has_deterministic_priority_and_clock_rollback_does_not_replay(self):
        paths = [make_wav(self.root / f"priority-{index}.wav") for index in range(3)]
        self.library.import_paths(paths, category="Comercial")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        fixed_a = self.programming.add_block("Fixo A", "Comercial", "12:00", [6], transition_policy="fixed")
        fixed_b = self.programming.add_block("Fixo B", "Comercial", "12:00", [6], transition_policy="fixed")
        regular = self.programming.add_block("Comercial regular", "Comercial", "12:00", [6])
        for block_id, media_id in zip((fixed_a, fixed_b, regular), media_ids):
            self.programming.add_block_item(block_id, media_id)

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 0))
        self.assertEqual(len(result["dispatch_ids"]), 1)
        winner = self.db.one("SELECT id,block_id,status FROM program_occurrences WHERE block_id=?", (fixed_a,))
        loser_rows = self.db.all("SELECT block_id,status FROM program_occurrences WHERE block_id IN (?,?) ORDER BY block_id", (fixed_b, regular))
        self.assertEqual(winner["status"], "planned")
        self.assertEqual([row["status"] for row in loser_rows], ["ignored", "ignored"])
        self.assertIn("prioridade", self.db.one("SELECT dispatch_reason FROM program_occurrences WHERE block_id=?", (fixed_b,))["dispatch_reason"])

        rollback = self.programming.dispatch_window(datetime(2026, 11, 15, 11, 59, 0))
        self.assertTrue(rollback["clock_rollback"])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 3)
        self.assertEqual(self.db.one("SELECT decision FROM scheduler_decisions WHERE decision='clock_rollback'")["decision"], "clock_rollback")

    def test_scheduler_same_minute_blocks_are_queued_by_block_id(self):
        files = [make_wav(self.root / f"collision-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        block_ids = []
        for index, media_id in enumerate(media_ids):
            block_id = self.programming.add_block(f"Colisão {index}", "Música", "12:00", [6])
            self.programming.add_block_item(block_id, media_id)
            block_ids.append(block_id)

        result = self.programming.dispatch_window(datetime(2026, 11, 15, 12, 0, 0))
        actual_blocks = [int(row["block_id"]) for row in self.db.all(
            "SELECT block_id FROM program_occurrences WHERE id IN (?,?) ORDER BY id", tuple(result["dispatch_ids"]),
        )]
        first_pending = self.programming.next_occurrence_item()
        self.assertEqual(actual_blocks, block_ids)
        self.assertEqual(len(result["dispatch_ids"]), 2)
        self.assertEqual(int(first_pending["occurrence_id"]), result["dispatch_ids"][0])

    def test_scheduler_bounds_long_recovery_and_audits_older_range(self):
        audio = make_wav(self.root / "horizon.wav")
        self.library.import_paths([audio], category="Música")
        media_id = int(self.db.one("SELECT id FROM media")["id"])
        block_id = self.programming.add_block("Diário", "Música", "12:00", list(range(7)))
        self.programming.add_block_item(block_id, media_id)
        now = datetime(2026, 11, 15, 12, 0)
        self.db.set_setting("scheduler_last_checked_at", "2026-10-01T12:00:00")

        result = self.programming.dispatch_window(now)

        self.assertEqual(len(result["missed_ids"]), 30)
        self.assertEqual(len(result["dispatch_ids"]), 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 31)
        self.assertTrue(self.db.one("SELECT id FROM scheduler_decisions WHERE decision='recovery_horizon'"))
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrence_items WHERE status='planned'")["n"], 1)

    def test_missing_file_is_snapshotted_as_pending_material(self):
        path = self.root / "offline" / "ausente.wav"
        media_id = self.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(path), "Ausente", "Música"))
        block_id = self.programming.add_block("Arquivo offline", "Música", "12:30", [6])
        self.programming.add_block_item(block_id, media_id)
        occurrence_id = self.programming.due(datetime(2026, 11, 15, 12, 30))[0]
        item = self.db.one("SELECT title,path,status FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))
        self.assertEqual((item["title"], item["path"], item["status"]), ("Ausente", str(path), "planned"))
        self.assertFalse(Path(item["path"]).exists())

    def _music_folder_with_tracks(self, names):
        folder_path = self.root / "Músicas" / "Rotação"
        folder_path.mkdir(parents=True, exist_ok=True)
        category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Música'")["id"])
        folder_id = self.library.save_folder("Rotação", str(folder_path), category_id)
        files = [make_wav(folder_path / f"{name}.wav") for name in names]
        self.library.import_paths(files, category="Música", folder_id=folder_id)
        expected = [int(row["id"]) for row in self.db.all("SELECT id FROM media WHERE folder_id=? ORDER BY title COLLATE NOCASE,path COLLATE NOCASE,id", (folder_id,))]
        return folder_id, expected

    def _jingle_folder_with_tracks(self, names, cooldown=2):
        folder_path = self.root / "Vinhetas" / "Rotação"
        folder_path.mkdir(parents=True, exist_ok=True)
        category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Vinheta'")["id"])
        folder_id = self.db.execute(
            "INSERT INTO media_folders(name,path,category_id,jingle_rotation_enabled,jingle_repeat_cooldown) VALUES(?,?,?,?,?)",
            ("Rodízio da emissora", str(folder_path), category_id, 1, cooldown),
        )
        files = [make_wav(folder_path / f"{name}.wav") for name in names]
        self.library.import_paths(files, category="Vinheta", folder_id=folder_id)
        return folder_id, [int(row["id"]) for row in self.db.all("SELECT id FROM media WHERE folder_id=? ORDER BY title COLLATE NOCASE,path COLLATE NOCASE,id", (folder_id,))]

    def test_sequential_folder_selection_is_ordered_and_persists_after_service_restart(self):
        folder_id, expected = self._music_folder_with_tracks(["Zulu", "Alpha", "Mike"])
        block_id = self.programming.add_block("Sequencial", "Música", "12:30", list(range(7)))
        selector_id = self.programming.add_block_folder(block_id, folder_id, "sequential")

        first_occurrence = self.programming.due(datetime(2026, 11, 16, 12, 30))[0]
        first = self.db.one("SELECT * FROM program_occurrence_items WHERE occurrence_id=?", (first_occurrence,))
        self.assertEqual((int(first["source_item_id"]), int(first["media_id"])), (selector_id, expected[0]))
        self.assertEqual((int(first["selection_folder_id"]), first["selection_mode"]), (folder_id, "sequential"))
        self.assertTrue(first["selected_at"])
        self.programming.set_occurrence_item_status(int(first["id"]), "completed")

        restarted = ProgrammingService(Database(self.db.path))
        second_occurrence = restarted.due(datetime(2026, 11, 17, 12, 30))[0]
        second = self.db.one("SELECT media_id FROM program_occurrence_items WHERE occurrence_id=?", (second_occurrence,))
        state = self.db.one("SELECT cursor,last_media_id FROM folder_rotation_state WHERE folder_id=? AND selection_mode='sequential'", (folder_id,))
        self.assertEqual(int(second["media_id"]), expected[1])
        self.assertEqual((int(state["cursor"]), int(state["last_media_id"])), (2, expected[1]))

    def test_random_folder_uses_persisted_shuffle_deck_without_repeats(self):
        folder_id, _expected = self._music_folder_with_tracks(["Alpha", "Bravo", "Charlie", "Delta"])
        self.programming = ProgrammingService(self.db, random.Random(73))
        block_id = self.programming.add_block("Aleatório", "Música", "12:30", list(range(7)))
        self.programming.add_block_folder(block_id, folder_id, "random")
        self.programming.add_block_folder(block_id, folder_id, "random")

        first_occurrence = self.programming.due(datetime(2026, 11, 16, 12, 30))[0]
        first_pair = [int(row["media_id"]) for row in self.db.all("SELECT media_id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (first_occurrence,))]
        self.assertEqual(len(set(first_pair)), 2)
        state = self.db.one("SELECT deck_json,cursor FROM folder_rotation_state WHERE folder_id=? AND selection_mode='random'", (folder_id,))
        deck = json.loads(state["deck_json"])
        self.assertEqual(int(state["cursor"]), 2)

        restarted = ProgrammingService(Database(self.db.path), random.Random(999))
        next_occurrence = restarted.due(datetime(2026, 11, 17, 12, 30))[0]
        next_pair = [int(row["media_id"]) for row in self.db.all("SELECT media_id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (next_occurrence,))]
        self.assertEqual(next_pair[0], deck[2])
        self.assertEqual(len(set(first_pair + next_pair)), 4)

    def test_folder_selector_does_not_repeat_within_occurrence_and_records_reason(self):
        folder_id, expected = self._music_folder_with_tracks(["Only track"])
        block_id = self.programming.add_block("Sem repetição local", "Música", "12:30", list(range(7)))
        self.programming.add_block_folder(block_id, folder_id, "sequential")
        self.programming.add_block_folder(block_id, folder_id, "sequential")

        occurrence_id = self.programming.due(datetime(2026, 11, 16, 12, 30))[0]
        items = self.db.all(
            "SELECT media_id,status,error_message FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position",
            (occurrence_id,),
        )
        self.assertEqual(int(items[0]["media_id"]), expected[0])
        self.assertEqual(items[0]["status"], "planned")
        self.assertIsNone(items[1]["media_id"])
        self.assertEqual(items[1]["status"], "failed")
        self.assertIn("já foram usados nesta ocorrência", items[1]["error_message"])

    def test_jingle_rotation_balances_usage_persists_cooldown_and_is_not_air_history(self):
        folder_id, members = self._jingle_folder_with_tracks(["ID", "Passagem", "Assinatura"])
        self.programming = ProgrammingService(self.db, random.Random(31))
        block_id = self.programming.add_block("Vinhetas", "Música", "12:30", list(range(7)))
        self.programming.add_block_folder(block_id, folder_id, "jingle_rotation")
        selected_ids = []
        first_snapshot = None
        for day in range(16, 22):
            occurrence_id = self.programming.due(datetime(2026, 11, day, 12, 30))[0]
            item = self.db.one("SELECT * FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))
            selected_ids.append(int(item["media_id"]))
            first_snapshot = first_snapshot or item

        for index, media_id in enumerate(selected_ids):
            self.assertNotIn(media_id, selected_ids[max(0, index - 2):index])
        state = self.db.one("SELECT counts_json,recent_ids_json,total_selected FROM jingle_rotation_state WHERE folder_id=?", (folder_id,))
        counts = {int(key): int(value) for key, value in json.loads(state["counts_json"]).items()}
        self.assertEqual(set(counts), set(members))
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        self.assertEqual(int(state["total_selected"]), 6)
        self.assertEqual((first_snapshot["selection_mode"], int(first_snapshot["selection_folder_id"])), ("jingle_rotation", folder_id))
        self.assertTrue(first_snapshot["selected_at"])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM playback_history")["n"], 0)

        restarted = ProgrammingService(Database(self.db.path), random.Random(32))
        next_occurrence = restarted.due(datetime(2026, 11, 22, 12, 30))[0]
        next_item = self.db.one("SELECT media_id,selection_mode FROM program_occurrence_items WHERE occurrence_id=?", (next_occurrence,))
        self.assertNotIn(int(next_item["media_id"]), selected_ids[-2:])
        self.assertEqual(next_item["selection_mode"], "jingle_rotation")
        self.assertEqual(int(self.db.one("SELECT total_selected FROM jingle_rotation_state WHERE folder_id=?", (folder_id,))["total_selected"]), 7)

    def test_jingle_cooldown_relaxes_for_small_pool_but_not_within_same_occurrence(self):
        folder_id, members = self._jingle_folder_with_tracks(["Um", "Dois"], cooldown=10)
        block_id = self.programming.add_block("Vinhetas compactas", "Música", "12:30", list(range(7)))
        self.programming.add_block_folder(block_id, folder_id, "jingle_rotation")
        self.programming.add_block_folder(block_id, folder_id, "jingle_rotation")
        occurrence_id = self.programming.due(datetime(2026, 11, 16, 12, 30))[0]
        items = self.db.all("SELECT media_id,status FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_id,))
        self.assertEqual({int(item["media_id"]) for item in items}, set(members))
        self.assertTrue(all(item["status"] == "planned" for item in items))
        next_occurrence = self.programming.due(datetime(2026, 11, 17, 12, 30))[0]
        next_items = self.db.all("SELECT media_id,status FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (next_occurrence,))
        self.assertEqual({int(item["media_id"]) for item in next_items}, set(members))
        self.assertTrue(all(item["status"] == "planned" for item in next_items))

    def test_configured_material_folder_and_category_do_not_delete_files(self):
        folder = self.root / "Spots"
        media_file = make_wav(folder / "spot.wav")
        category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Comercial'")["id"])
        folder_id = self.library.save_folder("Spots", str(folder), category_id)
        self.library.import_paths([folder], category="Comercial", folder_id=folder_id)
        row = self.db.one("SELECT folder_id,category FROM media WHERE path=?", (str(media_file.resolve()),))
        self.assertEqual(int(row["folder_id"]), folder_id)
        self.assertEqual(row["category"], "Comercial")
        self.library.deactivate_folder(folder_id)
        self.assertTrue(media_file.is_file())
        self.assertEqual(int(self.db.one("SELECT COUNT(*) AS n FROM media WHERE path=?", (str(media_file.resolve()),))["n"]), 1)

    def test_timeline_validates_time_and_fires_once_per_day_and_minute(self):
        path = make_wav(self.root / "Ad.wav")
        self.library.import_paths([path])
        media_id = int(self.db.one("SELECT id FROM media")["id"])
        with self.assertRaises(ValueError):
            self.timeline.add("25:70", [6], "Invalid", media_id, "Comercial")
        self.timeline.add("12:30", [6], "Weekend spot", media_id, "Comercial")
        now = datetime(2026, 11, 15, 12, 30)  # Sunday
        due = self.timeline.due(now)
        self.assertEqual(len(due), 1)
        self.assertEqual(int(due[0]["media_id"]), media_id)
        self.assertEqual(self.timeline.due(now), [])
        self.assertEqual(self.timeline.due(datetime(2026, 11, 16, 12, 30)), [])

    def test_timeline_tracks_missing_media_without_crashing_event_list(self):
        event_id = self.db.execute("INSERT INTO timeline_events(time_of_day,title,media_id) VALUES('12:00','Old file',NULL)")
        row = next(r for r in self.timeline.events() if int(r["id"]) == event_id)
        self.assertIsNone(row["media_title"])

    def test_category_detection_uses_folder_context(self):
        self.assertEqual(media_category(Path("/library/vinhetas/station.wav")), "Vinheta")
        self.assertEqual(media_category(Path("/library/music/artist-title.mp3")), "Música")


if __name__ == "__main__":
    unittest.main()

