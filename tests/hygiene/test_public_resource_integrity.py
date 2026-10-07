from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROMPT_REQUIRED_FIELDS = {
    "id",
    "sourceId",
    "title",
    "prompt",
    "description",
    "coverUrl",
    "referenceImageUrls",
    "tags",
    "author",
    "sourceUrl",
    "createdAt",
    "imageMode",
    "imageModel",
}
PROMPT_ALLOWED_FIELDS = PROMPT_REQUIRED_FIELDS | {"imageSize", "imageCount"}
PROMPT_STRING_FIELDS = {
    "id",
    "sourceId",
    "title",
    "prompt",
    "description",
    "coverUrl",
    "author",
    "sourceUrl",
    "createdAt",
    "imageMode",
    "imageModel",
}
PROMPT_SOURCE_HASHES = {
    "banana-prompt-quicker": "0ae590d56820d1d9716691e96410ee6869dec28a7261de397f74ad2f6cbedda5",
    "freestylefly-gpt-image-2": "6dd7eed617ccd629e2c96e1adeb4cf23640f86d6e1769aaa98ebee9de94e1a30",
    "awesome-gpt-image": "e5508cad635279cd2c9ddda70d422ad9de8700e7e349e463f2fe433e562a99ec",
    "awesome-gpt4o-image-prompts": "ece926584179496d426bd9703c0ea30a13b89a9ce0101bb340056d049e58979d",
    "youmind-gpt-image-2": "4d343babe6bc0e9b5aecc57e2cc7b5afa774a8d386c33f9b3b3a6123f18e748a",
    "youmind-nano-banana-pro": "61ea3a3a3eba2d9fcb3f21bcaa99a8ac13c720d39b20a7748ce951ddcc3104c9",
}
PROMPT_LICENSE_HASHES = {
    "banana-prompt-quicker": "b6cf424c887549a8176ce0c522b800a9f292bdc809f4cbc311e2d649b42d9a21",
    "freestylefly-gpt-image-2": "27a75c48bac29eb78f43c19f75c4e175974c8f1046d848d5562eae1ead2f1176",
    "awesome-gpt-image": "65d0863319198cb6f7a4d4bac395befd4099681bc371fd1a2a9ad9e35c02a738",
    "awesome-gpt4o-image-prompts": "f8131f554150f4865eb43845e3148f3e75c6ed664a6b2326a00dfc7fc1a7b976",
    "youmind-gpt-image-2": "cdf642acded97160b992064916cc1c7490ce5a8d8ccc70d8d6456e16976f9bd0",
    "youmind-nano-banana-pro": "2b2ffb76ba44bf79a3f29023332fad2d8bd70ef9dd27ff9e7258bf1a213a08bb",
}

VENDOR_HASHES = {
    "web/vendor/js/lucide.js": "187a756625c5ce7499c207d1b0d1cf4e1ab95e3f666c7e0cd0fafc3e6842d040",
    "web/vendor/js/three-0.160.0.module.js": "76dea8151bc9352aef3528b4262e249b2604f62543828328db978d060d61a495",
    "web/vendor/js/tailwindcss-cdn.js": "d11003b2f6fbe0ca79340ecd3349dffe61e11a3feb224b3fdfa3d24900f70e83",
    "web/vendor/css/fonts.css": "f978fffa0369f8b433ee2b1aae442a7e29cdd01fb760b29034de71f820814cbb",
    "web/vendor/fonts/SourceHanSansCN-Normal.otf": "9fc9ad5b64f086a2b342087865623923c216e346f879142a84df37c7ffd323c5",
    "web/vendor/fonts/SourceHanSansCN-Medium.otf": "a94e558a2fe972bee4f46bce0843abff37063fd68c33f1e7d9058f6f09432b01",
    "web/vendor/fonts/SourceHanSansCN-Bold.otf": "62383707c086a32f3afd5e293f34c7eff64c7fea31f579fdc6cbe34d920519a6",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_prompt_registry_snapshot_hashes_and_v1_record_schema() -> None:
    """重建快照必须维持现有页面使用的清单契约和固定上游记录 schema。"""
    repo_root = _repo_root()
    manifest_path = repo_root / "web/prompt-registry/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schemaVersion"] == 1
    assert manifest["upstreamCommit"] == "8df9939175e263538fa39757e23c80f37ab4da14"
    assert manifest["promptRecordSchema"]["sha256"] == (
        "908ce723b38175e11f7a329466dcdc86347cff948dd9fc301bd78c0dedc00247"
    )
    assert isinstance(manifest.get("sources"), list)

    total = 0
    for source in manifest["sources"]:
        for field in (
            "id",
            "name",
            "license",
            "copyright",
            "homepage",
            "sourceUrl",
            "path",
            "count",
            "sha256",
            "sourceSha256",
            "localSha256",
            "localTransformation",
            "licenseUrl",
            "licenseSha256",
        ):
            assert field in source
        assert manifest["upstreamCommit"] in source["sourceUrl"]
        assert "/main/" not in source["sourceUrl"]
        assert source["localTransformation"] == "identity-copy"
        assert source["sourceSha256"] == PROMPT_SOURCE_HASHES[source["id"]]
        assert source["licenseSha256"] == PROMPT_LICENSE_HASHES[source["id"]]
        assert source["sha256"] == source["sourceSha256"] == source["localSha256"]
        assert "/main/" not in source["licenseUrl"]

        relative_path = Path(source["path"])
        assert not relative_path.is_absolute() and ".." not in relative_path.parts
        source_path = repo_root / "web/prompt-registry" / relative_path
        assert source_path.is_file()
        assert _sha256(source_path) == source["localSha256"]
        records = json.loads(source_path.read_text(encoding="utf-8"))
        assert isinstance(records, list)
        assert len(records) == source["count"]
        total += len(records)

        for record in records:
            assert record["sourceId"] == source["id"]
            assert PROMPT_REQUIRED_FIELDS <= record.keys()
            assert record.keys() <= PROMPT_ALLOWED_FIELDS
            assert all(isinstance(record[field], str) for field in PROMPT_STRING_FIELDS)
            assert isinstance(record["tags"], list)
            assert isinstance(record["referenceImageUrls"], list)
            assert all(isinstance(tag, str) for tag in record["tags"])
            assert all(isinstance(url, str) for url in record["referenceImageUrls"])

    assert total == manifest["total"] == 1230
    source_ids = {source["id"] for source in manifest["sources"]}
    excluded_sources = {source["id"]: source for source in manifest["excludedSources"]}
    assert source_ids == set(PROMPT_SOURCE_HASHES)
    assert "davidwu-gpt-image2-prompts" not in source_ids
    excluded = excluded_sources["davidwu-gpt-image2-prompts"]
    assert excluded["status"] == "EXCLUDED_LICENSE_TEXT_INCOMPLETE"
    assert excluded["sourceSha256"] == "bc61473b87e53acd514d14e1936f3490fe7d9db3cf004d5730dda9f28cda1dbf"
    assert "README" in excluded["reason"] and "MIT" in excluded["reason"]
    assert not (repo_root / "web/prompt-registry/sources/davidwu-gpt-image2-prompts.json").exists()


def test_vendor_assets_match_recorded_source_and_local_hashes() -> None:
    """vendor 账本中的直接源副本、字体白名单与源/本地哈希必须一致。"""
    repo_root = _repo_root()
    manifest_text = (repo_root / "web/vendor/MANIFEST.md").read_text(encoding="utf-8")
    licenses_text = (repo_root / "web/vendor/LICENSES.md").read_text(encoding="utf-8")

    for relative_path, expected_hash in VENDOR_HASHES.items():
        actual_hash = _sha256(repo_root / relative_path)
        assert actual_hash == expected_hash, relative_path
        assert expected_hash.upper() in manifest_text.upper(), relative_path

    otf_paths = {
        path.relative_to(repo_root).as_posix()
        for path in (repo_root / "web/vendor/fonts").glob("*.otf")
    }
    assert otf_paths == {
        "web/vendor/fonts/SourceHanSansCN-Bold.otf",
        "web/vendor/fonts/SourceHanSansCN-Medium.otf",
        "web/vendor/fonts/SourceHanSansCN-Normal.otf",
    }
    for license_hash in PROMPT_LICENSE_HASHES.values():
        assert license_hash in (repo_root / "web/prompt-registry/NOTICE.md").read_text(encoding="utf-8")
    assert "Version 2.005" in licenses_text
    assert "BLOCKED" in licenses_text
    assert "全仓 SBOM 与完整 NOTICE 仍缺失" in licenses_text
