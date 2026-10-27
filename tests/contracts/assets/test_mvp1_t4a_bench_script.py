"""MVP1-T4a：基准脚本参数与缺失模型错误冒烟测试。"""

import json
import sys
import types

from scripts.bench import mvp1_t4a_embed_bench as bench


def test_bench_parser_accepts_reproduction_arguments():
    args = bench.build_parser().parse_args(
        ["--root", "dataset", "--queries", "queries.jsonl"]
    )

    assert args.root.name == "dataset"
    assert args.queries.name == "queries.jsonl"
    assert args.max_images == 500
    assert args.warmup_images == 5


def test_missing_model_returns_clear_nonzero_error(tmp_path, monkeypatch, capsys):
    image_root = tmp_path / "images"
    image_root.mkdir()
    rows = []
    for index in range(30):
        name = f"asset_{index + 1:03}.png"
        (image_root / name).write_bytes(b"placeholder")
        rows.append({"image": name, "query": f"第{index + 1}张图的中文场景描述"})
    query_file = tmp_path / "queries.jsonl"
    query_file.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    class MissingModel:
        @classmethod
        def from_pretrained(cls, model_id, **kwargs):
            raise OSError("model missing for smoke test")

    transformers = types.ModuleType("transformers")
    transformers.AutoModel = MissingModel
    transformers.AutoProcessor = MissingModel
    monkeypatch.setitem(sys.modules, "transformers", transformers)

    exit_code = bench.main(
        [
            "--root",
            str(image_root),
            "--queries",
            str(query_file),
            "--_single-model",
            "chinese_clip",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code != 0
    assert "无法加载模型 OFA-Sys/chinese-clip-vit-base-patch16" in captured.err
    assert "model missing for smoke test" in captured.err
    assert "请确认临时基准环境" in captured.err
