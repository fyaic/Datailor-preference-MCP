from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preference_agent.capture_runner import CaptureConfig, CaptureRunner
from preference_agent.embeddings import GLMEmbeddingBackend, HashEmbeddingBackend
from preference_agent.recall import MultiRouteRecallEngine, RecallConfig, RecallInput


class RecallPipelineTests(unittest.TestCase):
    def test_multi_route_recall_finds_semantic_preference_without_old_marker(self) -> None:
        engine = MultiRouteRecallEngine(
            embedding_backend=HashEmbeddingBackend(dimensions=128),
            config=RecallConfig(strategy="multi", semantic_threshold=0.20, final_threshold=0.12),
        )
        candidates = engine.recall_batch(
            [
                RecallInput(source="sample:1", content="Keep replies shorter and less verbose."),
                RecallInput(source="sample:2", content="What is the weather in Beijing today?"),
            ]
        )
        texts = "\n".join(item.content for item in candidates)
        self.assertIn("Keep replies shorter", texts)
        self.assertNotIn("weather in Beijing", texts)

    def test_semantic_capture_job_keeps_single_markdown_output_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.jsonl"
            source.write_text(
                "\n".join(
                    [
                        json.dumps({"role": "user", "content": "Keep replies shorter and less verbose."}),
                        json.dumps({"role": "user", "content": "What is the weather in Beijing today?"}),
                    ]
                ),
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "data" / ".debug-capture",
                checkpoint_dir=root / "data" / ".capture-state",
                store_path=root / "data" / "personal-preferences.md",
                mode="semantic-recall",
                recall_strategy="multi",
                recall_batch_size=2,
                batch_size=2,
                max_minutes=1,
            )
            with patch.dict(
                os.environ,
                {
                    "PREFERENCE_EMBEDDING_BACKEND": "hash",
                    "PREFERENCE_RECALL_SEMANTIC_THRESHOLD": "0.15",
                    "PREFERENCE_RECALL_FINAL_THRESHOLD": "0.08",
                },
            ):
                result = CaptureRunner(config).run(source)
            self.assertGreaterEqual(result.candidates_written, 1)
            self.assertEqual(result.candidate_file, "")
            self.assertEqual(result.summary_file, "")
            self.assertTrue(Path(result.store_file).exists())
            self.assertTrue(Path(result.checkpoint_file).exists())

    def test_glm_embedding_request_uses_expected_endpoint_and_dimensions(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "data": [
                            {"index": 0, "embedding": [1.0, 0.0]},
                            {"index": 1, "embedding": [0.0, 1.0]},
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["authorization"] = request.headers.get("Authorization")
            return FakeResponse()

        with tempfile.TemporaryDirectory() as temp, patch("urllib.request.urlopen", fake_urlopen):
            with patch.dict(
                os.environ,
                {
                    "PREFERENCE_EMBEDDING_API_KEY": "test-key",
                    "PREFERENCE_EMBEDDING_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
                    "PREFERENCE_EMBEDDING_MODEL": "embedding-3",
                    "PREFERENCE_EMBEDDING_DIMENSIONS": "2",
                    "PREFERENCE_EMBEDDING_CACHE_PATH": str(Path(temp) / "cache.json"),
                },
            ):
                vectors = GLMEmbeddingBackend().embed_texts(["shorter", "outline first"])
        self.assertEqual(captured["url"], "https://open.bigmodel.cn/api/paas/v4/embeddings")
        self.assertEqual(captured["body"]["model"], "embedding-3")
        self.assertEqual(captured["body"]["dimensions"], 2)
        self.assertEqual(captured["authorization"], "Bearer test-key")
        self.assertEqual(len(vectors), 2)


if __name__ == "__main__":
    unittest.main()
