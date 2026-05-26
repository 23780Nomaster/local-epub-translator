"""Checkpoint manager for resumable EPUB translation."""
import json
import os
import tempfile


class CheckpointManager:
    def __init__(self, input_path, output_path, mode, target_lang, completed=None):
        self.input_path = os.path.abspath(input_path)
        self.output_path = os.path.abspath(output_path)
        self.input_mtime = os.path.getmtime(self.input_path)
        self.mode = mode
        self.target_lang = target_lang
        self.completed = completed or []
        self._completed_map = {}
        for entry in self.completed:
            self._completed_map.setdefault(entry["file"], {})[entry["idx"]] = entry["translated"]

    @property
    def checkpoint_path(self):
        return self.output_path + ".checkpoint"

    def is_completed(self, file_name, idx):
        return idx in self._completed_map.get(file_name, {})

    def get_translated(self, file_name, idx):
        return self._completed_map.get(file_name, {}).get(idx)

    def mark_completed(self, file_name, idx, translated):
        self._completed_map.setdefault(file_name, {})[idx] = translated
        self.completed.append({
            "file": file_name,
            "idx": idx,
            "translated": translated,
        })
        self.save()

    def save(self):
        data = {
            "input_path": self.input_path,
            "input_mtime": self.input_mtime,
            "output_path": self.output_path,
            "mode": self.mode,
            "target_lang": self.target_lang,
            "completed": self.completed,
        }
        tmp_path = self.checkpoint_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.checkpoint_path)

    def delete(self):
        try:
            os.remove(self.checkpoint_path)
        except OSError:
            pass

    @classmethod
    def load(cls, input_path, output_path, mode, target_lang):
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        cp_path = output_path + ".checkpoint"

        if not os.path.exists(cp_path):
            return None

        try:
            with open(cp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError, KeyError):
            print("Warning: checkpoint file corrupted, starting fresh.")
            return None

        # Validate input file hasn't changed
        current_mtime = os.path.getmtime(input_path)
        if data.get("input_mtime", 0) != current_mtime:
            print("Warning: input file has changed since checkpoint, starting fresh.")
            return None

        # Validate mode and target_lang match
        if data.get("mode") != mode:
            print("Warning: mode mismatch with checkpoint, starting fresh.")
            return None
        if data.get("target_lang") != target_lang:
            print("Warning: target language mismatch with checkpoint, starting fresh.")
            return None

        manager = cls(
            input_path=input_path,
            output_path=output_path,
            mode=mode,
            target_lang=target_lang,
            completed=data.get("completed", []),
        )
        return manager
