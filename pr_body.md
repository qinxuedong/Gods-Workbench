QIN-398 | Issue: #398 | Linear: https://linear.app/gw/issue/QIN-398

## 改动文件
- tests/runtime/test_asset_registry_audit_sink.py

## 测试
- `GW_RUNNINGHUB_API_KEY=dummy-for-tests python -m pytest -q -p no:cacheprovider tests/runtime/test_asset_registry_audit_sink.py` → 9 passed
- `GW_RUNNINGHUB_API_KEY=dummy-for-tests python -m pytest -q -p no:cacheprovider` → 3250 passed, 94 skipped, 1 warning

## 待本地实机验收
- 无

## 范围外发现
- 无

执行人: jules
