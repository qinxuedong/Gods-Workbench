#!/usr/bin/env python3
"""
tools/sync_linear_relations.py
Linear 工单底层 Blocking 物理阻断关系同步与核验门禁工具。

硬门禁规则：
1. 严禁仅在工单 Markdown 正文中口头描述依赖；必须在 Linear 底层建立真实的 issueRelationCreate(type: "blocks")。
2. 凡涉及先后阻断或跨阶段依赖，必须执行 --verify 确保退出码为 0。
3. 主干合入前置拦截：--check-merge <identifier> 校验所有前置 blocker 是否均已达到 Done 终态。
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

# 跨阶段与阶段内部的权威物理阻断定义 (Blocker -> Blocked)
CANONICAL_BLOCKING_RELATIONS = [
    # C13 -> C14
    ("QIN-215", "QIN-216"),  # C13-T4 收口 -> C14-T1
    ("QIN-216", "QIN-217"),  # C14-T1 -> C14-T2
    ("QIN-217", "QIN-218"),  # C14-T2 -> C14-T3
    ("QIN-218", "QIN-219"),  # C14-T3 -> C14-T4
    # C14 -> C15
    ("QIN-219", "QIN-220"),  # C14-T4 收口 -> C15-T1
    ("QIN-220", "QIN-221"),  # C15-T1 -> C15-T2
    ("QIN-220", "QIN-222"),  # C15-T1 -> C15-T3
    ("QIN-221", "QIN-223"),  # C15-T2 -> C15-T4
    ("QIN-222", "QIN-223"),  # C15-T3 -> C15-T4
    # C16
    ("QIN-205", "QIN-224"),  # C11-T5 全站里程碑 -> C16-T1
    ("QIN-224", "QIN-225"),  # C16-T1 -> C16-T2
    ("QIN-224", "QIN-226"),  # C16-T1 -> C16-T3
    ("QIN-225", "QIN-227"),  # C16-T2 -> C16-T4
    ("QIN-226", "QIN-227"),  # C16-T3 -> C16-T4
    # C15/C16 -> C17 (跨阶段硬阻断)
    ("QIN-223", "QIN-228"),  # C15-T4 收口 -> C17-T1
    ("QIN-227", "QIN-228"),  # C16-T4 收口 -> C17-T1
    # C17 阶段内部
    ("QIN-228", "QIN-229"),  # C17-T1 -> C17-T2
    ("QIN-228", "QIN-230"),  # C17-T1 -> C17-T3
    ("QIN-228", "QIN-231"),  # C17-T1 -> C17-T4
    ("QIN-228", "QIN-232"),  # C17-T1 -> C17-T5
    ("QIN-229", "QIN-232"),  # C17-T2 -> C17-T5
    ("QIN-230", "QIN-232"),  # C17-T3 -> C17-T5
    ("QIN-231", "QIN-232"),  # C17-T4 -> C17-T5
    # C18 (全站统一命令总线与多模态检索)
    ("QIN-205", "QIN-233"),  # C11-T5 -> C18-T1
    ("QIN-233", "QIN-234"),  # C18-T1 -> C18-T2
    ("QIN-233", "QIN-235"),  # C18-T1 -> C18-T3
    ("QIN-234", "QIN-235"),  # C18-T2 -> C18-T3
    ("QIN-229", "QIN-236"),  # C17-T2 (Kev-0.8B) -> C18-T4 (SQLite-vec / FTS5)
    ("QIN-235", "QIN-236"),  # C18-T3 -> C18-T4
    ("QIN-233", "QIN-237"),  # C18-T1 -> C18-T5 (C18 收口)
    ("QIN-234", "QIN-237"),  # C18-T2 -> C18-T5
    ("QIN-235", "QIN-237"),  # C18-T3 -> C18-T5
    ("QIN-236", "QIN-237"),  # C18-T4 -> C18-T5
]


def execute_graphql(query: str, variables: dict | None = None) -> dict:
    api_key = os.getenv("LINEAR_API_KEY")
    if not api_key:
        print("[ERROR] 环境变量 LINEAR_API_KEY 未设置！无法执行 Linear 接口调用。", file=sys.stderr)
        sys.exit(2)

    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    req = urllib.request.Request(
        LINEAR_GRAPHQL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "errors" in data and data["errors"]:
                print(f"[ERROR] GraphQL 错误: {json.dumps(data['errors'], indent=2, ensure_ascii=False)}", file=sys.stderr)
                sys.exit(1)
            return data.get("data", {})
    except urllib.error.HTTPError as e:
        print(f"[ERROR] HTTP 请求失败 ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)


def fetch_all_issues(identifiers: set[str]) -> dict[str, dict]:
    """根据 identifier 集合获取 issues 及其 relations"""
    numbers = []
    for ident in identifiers:
        if ident.startswith("QIN-"):
            try:
                numbers.append(int(ident.replace("QIN-", "")))
            except ValueError:
                pass

    query = """
    query GetIssues($numbers: [Float!]!) {
      issues(filter: { number: { in: $numbers } }, first: 100) {
        nodes {
          id
          identifier
          title
          state {
            name
            type
          }
          relations {
            nodes {
              id
              type
              relatedIssue {
                id
                identifier
                title
                state {
                  name
                  type
                }
              }
            }
          }
          inverseRelations {
            nodes {
              id
              type
              issue {
                id
                identifier
                title
                state {
                  name
                  type
                }
              }
            }
          }
        }
      }
    }
    """
    res = execute_graphql(query, {"numbers": numbers})
    issues_by_ident = {}
    for node in res.get("issues", {}).get("nodes", []):
        issues_by_ident[node["identifier"]] = node
    return issues_by_ident


def sync_relations() -> bool:
    print("=== 开始同步 Linear 物理阻断关系 (issueRelationCreate type: blocks) ===")
    all_idents = set()
    for b_from, b_to in CANONICAL_BLOCKING_RELATIONS:
        all_idents.add(b_from)
        all_idents.add(b_to)

    issues = fetch_all_issues(all_idents)
    created_count = 0
    already_count = 0

    mutation = """
    mutation CreateRelation($input: IssueRelationCreateInput!) {
      issueRelationCreate(input: $input) {
        success
        issueRelation {
          id
          type
        }
      }
    }
    """

    for blocker_ident, blocked_ident in CANONICAL_BLOCKING_RELATIONS:
        blocker = issues.get(blocker_ident)
        blocked = issues.get(blocked_ident)
        if not blocker or not blocked:
            print(f"[WARN] 工单缺失，跳过: {blocker_ident} -> {blocked_ident} (blocker={bool(blocker)}, blocked={bool(blocked)})")
            continue

        # 检查是否已存在 blocks 关系
        # 在 Linear 中: blocker.relations 中有 type == 'blocks' 且 relatedIssue == blocked
        # 或 blocked.inverseRelations 中有 type == 'blocks' 且 issue == blocker
        exists = False
        for rel in blocker.get("relations", {}).get("nodes", []):
            if rel["type"] == "blocks" and rel["relatedIssue"]["identifier"] == blocked_ident:
                exists = True
                break
        if not exists:
            for rel in blocked.get("inverseRelations", {}).get("nodes", []):
                if rel["type"] == "blocks" and rel["issue"]["identifier"] == blocker_ident:
                    exists = True
                    break

        if exists:
            already_count += 1
            continue

        print(f"[SYNC] 正在建立物理阻断: {blocker_ident} -> blocks -> {blocked_ident} ...")
        # IssueRelationCreateInput:
        # issueId: 源工单 (blocker)
        # relatedIssueId: 目标工单 (blocked)
        # type: "blocks" 表示 issueId blocks relatedIssueId
        res = execute_graphql(mutation, {
            "input": {
                "issueId": blocker["id"],
                "relatedIssueId": blocked["id"],
                "type": "blocks",
            }
        })
        if res.get("issueRelationCreate", {}).get("success"):
            print(f"  [OK] 成功建立: {blocker_ident} blocks {blocked_ident}")
            created_count += 1
        else:
            print(f"  [FAILED] 建立失败: {res}")

    print(f"=== 同步完成: 新建 {created_count} 条，已存在 {already_count} 条 ===")
    return True


def verify_relations() -> bool:
    print("=== 开始核验 Linear 物理阻断关系 (Zero-Fake-Relation Verification) ===")
    all_idents = set()
    for b_from, b_to in CANONICAL_BLOCKING_RELATIONS:
        all_idents.add(b_from)
        all_idents.add(b_to)

    issues = fetch_all_issues(all_idents)
    missing = []
    verified = []

    for blocker_ident, blocked_ident in CANONICAL_BLOCKING_RELATIONS:
        blocker = issues.get(blocker_ident)
        blocked = issues.get(blocked_ident)
        if not blocker or not blocked:
            missing.append(f"{blocker_ident} -> {blocked_ident} (工单未找到: blocker={bool(blocker)}, blocked={bool(blocked)})")
            continue

        exists = False
        for rel in blocker.get("relations", {}).get("nodes", []):
            if rel["type"] == "blocks" and rel["relatedIssue"]["identifier"] == blocked_ident:
                exists = True
                break
        if not exists:
            for rel in blocked.get("inverseRelations", {}).get("nodes", []):
                if rel["type"] == "blocks" and rel["issue"]["identifier"] == blocker_ident:
                    exists = True
                    break

        if exists:
            verified.append(f"{blocker_ident} -> blocks -> {blocked_ident}")
        else:
            missing.append(f"{blocker_ident} -> blocks -> {blocked_ident} [底层物理关联缺失!]")

    print(f"核验结果统计: 已验证通过 {len(verified)} 条，缺失 {len(missing)} 条")
    if missing:
        print("[FAIL] 以下预期物理阻断关系在 Linear 中缺失:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return False
    else:
        print("[PASS] 全量预期阻断关系 100% 具备底层物理绑定，无虚假关联！")
        return True


def check_merge(target_ident: str) -> bool:
    """检查指定工单的上游 blocker 是否全为 Done"""
    print(f"=== 检查工单 {target_ident} 主干合入资格 (反冒进合入硬门禁) ===")
    issues = fetch_all_issues({target_ident})
    target = issues.get(target_ident)
    if not target:
        print(f"[ERROR] 未找到工单 {target_ident}", file=sys.stderr)
        return False

    # 查找该工单被哪些工单 blocks (即 is blocked by)
    blockers = []
    for rel in target.get("inverseRelations", {}).get("nodes", []):
        if rel["type"] == "blocks":
            blockers.append(rel["issue"])

    print(f"工单 {target_ident} ({target['state']['name']}) 的前置阻断工单列表 ({len(blockers)} 项):")
    if not blockers:
        print("  - [无前置阻断] 具备合入资格！")
        return True

    unresolved = []
    for b in blockers:
        state_type = b.get("state", {}).get("type")
        state_name = b.get("state", {}).get("name")
        is_done = state_type in ("completed", "canceled") or state_name in ("Done", "Canceled", "Merged")
        status_flag = "[OK - 已完成]" if is_done else "[BLOCKING - 未完成!]"
        print(f"  {status_flag} {b['identifier']}: {b['title']} (状态: {state_name})")
        if not is_done:
            unresolved.append(b)

    if unresolved:
        print(f"\n[REJECT] 前置阻断拦截！工单 {target_ident} 仍有 {len(unresolved)} 项前置工单未完成，绝对禁止合入 master！", file=sys.stderr)
        return False
    else:
        print(f"\n[PERMIT] 验证通过！工单 {target_ident} 的所有前置阻断工单均已达成 Done，准予合入 master！")
        return True


def main():
    parser = argparse.ArgumentParser(description="Linear 物理阻断关系同步与门禁核验工具")
    parser.add_argument("--sync", action="store_true", help="同步并建立缺失的底层物理阻断关系")
    parser.add_argument("--verify", action="store_true", help="核验全量物理阻断关系是否存在，不符时退出码为 1")
    parser.add_argument("--check-merge", type=str, metavar="IDENTIFIER", help="核验指定工单是否满足合入 master 资格（所有前置 blocker 必须为 Done）")

    args = parser.parse_args()
    if not (args.sync or args.verify or args.check_merge):
        parser.print_help()
        sys.exit(1)

    if args.sync:
        if not sync_relations():
            sys.exit(1)

    if args.verify:
        if not verify_relations():
            sys.exit(1)

    if args.check_merge:
        if not check_merge(args.check_merge):
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
