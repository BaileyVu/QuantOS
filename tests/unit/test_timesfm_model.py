import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import Mock, patch

from quantos.paper.research import timesfm_model


class TimesFMModelLoaderTest(unittest.TestCase):
    def _fake_imports(self, weights_path):
        torch = types.ModuleType("torch")
        torch.set_num_threads = Mock()
        torch_module = types.SimpleNamespace(device=types.SimpleNamespace(type="cpu"))
        model = Mock(model=torch_module)
        model.compile = Mock()
        model_class = Mock()
        model_class.from_pretrained.return_value = model

        config_type = Mock(side_effect=lambda **kwargs: kwargs)
        timesfm = types.ModuleType("timesfm")
        timesfm.ForecastConfig = config_type
        timesfm.TimesFM_2p5_200M_torch = model_class

        hub = types.ModuleType("huggingface_hub")
        hub.hf_hub_download = Mock(return_value=str(weights_path))
        return torch, timesfm, hub, model, model_class, config_type

    def test_pinned_loader_verifies_hash_and_compiles_cpu_config(self):
        with TemporaryDirectory() as tmp:
            weights = Path(tmp) / "weights.safetensors"
            weights.write_bytes(b"synthetic checkpoint bytes")
            digest = hashlib.sha256(weights.read_bytes()).hexdigest()
            fake = self._fake_imports(weights)
            torch, timesfm, hub, model, model_class, config_type = fake
            with patch.object(timesfm_model, "WEIGHTS_SHA256", digest), \
                 patch.object(timesfm_model.importlib.metadata, "version", side_effect=lambda n: f"{n}-pin"), \
                 patch.dict(sys.modules, {"torch": torch, "timesfm": timesfm,
                                          "huggingface_hub": hub}):
                loaded = timesfm_model.load_timesfm25(cache_dir=Path(tmp) / "cache",
                                                       local_files_only=True)

            self.assertEqual(torch.set_num_threads.call_args.args, (2,))
            hub.hf_hub_download.assert_called_once_with(
                repo_id=timesfm_model.MODEL_ID,
                filename="model.safetensors",
                revision=timesfm_model.MODEL_REVISION,
                cache_dir=str((Path(tmp) / "cache").resolve()),
                token=False,
                local_files_only=True,
            )
            model_class.from_pretrained.assert_called_once_with(
                timesfm_model.MODEL_ID,
                revision=timesfm_model.MODEL_REVISION,
                cache_dir=str((Path(tmp) / "cache").resolve()),
                token=False,
                torch_compile=False,
                local_files_only=True,
            )
            config_type.assert_called_once_with(
                max_context=512, max_horizon=128, normalize_inputs=True,
                use_continuous_quantile_head=True, fix_quantile_crossing=True,
                per_core_batch_size=1,
            )
            model.compile.assert_called_once_with({
                "max_context": 512, "max_horizon": 128,
                "normalize_inputs": True, "use_continuous_quantile_head": True,
                "fix_quantile_crossing": True, "per_core_batch_size": 1,
            })
            self.assertIs(loaded.model, model)
            self.assertEqual(loaded.metadata["weights_sha256"], digest)
            self.assertEqual(loaded.metadata["device"], "cpu")
            self.assertEqual(loaded.metadata["dependencies"]["torch"], "torch-pin")

    def test_bad_checkpoint_hash_fails_before_model_load(self):
        with TemporaryDirectory() as tmp:
            weights = Path(tmp) / "weights.safetensors"
            weights.write_bytes(b"untrusted synthetic checkpoint")
            fake = self._fake_imports(weights)
            torch, timesfm, hub, _, model_class, _ = fake
            with patch.dict(sys.modules, {"torch": torch, "timesfm": timesfm,
                                          "huggingface_hub": hub}):
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    timesfm_model.load_timesfm25(cache_dir=Path(tmp) / "cache")
            model_class.from_pretrained.assert_not_called()


if __name__ == "__main__":
    unittest.main()
