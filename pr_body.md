QIN-389 | Issue: #389 | Linear: https://linear.app/qin/issue/QIN-389

## 改动文件
- tests/runtime/test_asset_visibility.py

## 测试
- `python -m pytest tests/runtime/test_asset_visibility.py -v -p no:cacheprovider` -> 6 passed
- `python -m pytest -v --no-header -p no:cacheprovider` -> 3192 passed, 93 skipped

## 待本地实机验收
- 无

## 范围外发现
- 无

执行人: jules
