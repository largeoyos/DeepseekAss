"""Live five-chapter acceptance test. Every application operation uses CLI."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from _local_test_credentials import load as load_credentials, save as save_credentials


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


class Run:
    def __init__(self, output):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.report = json.loads((output / "report.json").read_text(encoding="utf-8")) if (output / "report.json").exists() else {
            "status": "prepared", "real_models": True, "cli_calls": [], "completed_chapters": [], "plans": {}, "checks": {}}
        if self.report.get("error"):
            self.report.setdefault("previous_failures", []).append(self.report.pop("error"))
        self.scenario = json.loads((ROOT / "scripts/five_chapter_scenario.json").read_text(encoding="utf-8"))
        self.env = os.environ.copy()
        self.sensitive = []
        save(output / "scenario.json", self.scenario)

    def checkpoint(self):
        save(self.output / "report.json", self.report)

    def note(self, text):
        print(text, flush=True)
        self.checkpoint()

    def cli(self, *args, stdin=None, authentication=False):
        process = subprocess.run([sys.executable, str(ROOT / "control_main.py"), "--username", self.username, *args],
            cwd=ROOT, env=self.env, input=stdin, capture_output=True, encoding="utf-8", timeout=1800)
        self.report["cli_calls"].append({"args": list(args), "exit_code": process.returncode})
        self.checkpoint()
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CLI 标准输出不是 JSON，测试停止") from exc
        require(process.returncode == 0 and result.get("ok"), str(result.get("error", {})))
        # Auth results are kept in memory/DPAPI only, never in reports.
        return result["data"]

    def book(self, *args, stdin=None):
        return self.cli(*args, "--book", self.report["book_id"], stdin=stdin)

    def setup(self):
        from config import Config
        from core.data_paths import resolve_data_root
        credential_path = Path(resolve_data_root()) / "users" / ".cli-novel-test.dpapi"
        if credential_path.exists():
            credentials = load_credentials(credential_path)
            self.username = credentials["username"]
            self.env["DSA_TEST_PASSWORD"] = credentials["password"]
            action = "grant" if credentials.get("registered") else "register"
        else:
            self.username = "cli_novel_test_" + uuid.uuid4().hex[:8]
            credentials = {"username": self.username, "password": secrets.token_urlsafe(32)}
            self.env["DSA_TEST_PASSWORD"] = credentials["password"]
            action = "register"
            # Save generated credentials before registration, so process failure
            # never leaves an unrecoverable newly-created account.
            save_credentials(credential_path, credentials)
        self.sensitive = [credentials["password"], Config.API_KEY]
        auth = self.cli("auth", action, "--password-env", "DSA_TEST_PASSWORD", "--name", "五章CLI实测",
                        "--expires-days", "1", "--allow-agent", "--allow-manage", "--allow-approve", authentication=True)
        credentials["registered"] = True
        save_credentials(credential_path, credentials)
        self.env["DEEPSEEKASS_CONTROL_TOKEN"] = auth["token"]
        self.sensitive.append(auth["token"])
        self.report["username"] = self.username
        self.note(f"测试账号：{self.username}；凭据已由 Windows DPAPI 保护。")
        if Config.API_KEY:
            config = {"text": {"api_key": Config.API_KEY, "base_url": Config.BASE_URL,
                               "model": os.environ.get("DEEPSEEK_MODEL", Config.MODEL_V4_FLASH)}}
            self.cli("models", "configure", "--input", "-", stdin=json.dumps(config))
        self.cli("control", "generation", "--enabled")
        save(self.output / "status.json", self.cli("status"))
        if not self.report.get("book_id"):
            self.title = self.scenario["title"] + "（CLI实测-" + uuid.uuid4().hex[:6] + "）"
            created = self.cli("project", "create", "--title", self.title)
            self.report["book_id"] = created["project"]["book_id"]
            self.report["book_title"] = self.title
            self.checkpoint()
        if not self.report["checks"].get("project_and_world_setup"):
            fields = {key: self.scenario[key] for key in ("protagonist_bio", "background_story", "writing_demand", "genre")}
            fields["author_plan"] = json.dumps(self.scenario["chapters"], ensure_ascii=False)
            self.book("project", "set", "--input", "-", stdin=json.dumps(fields, ensure_ascii=False))
            seed = {"reason": "初始人物与场景设定，不包含未来剧情事实", "operations": [
                {"operation": "create", "entity_type": "character", "entity_id": "test_shenyan", "scope": "global", "payload": {"name": "沈砚", "traits": "临时邮差，擅长辨认旧邮戳和维修机械"}},
                {"operation": "create", "entity_type": "location", "entity_id": "test_wugang", "scope": "global", "payload": {"name": "雾港", "description": "近现代架空海港，旧邮船即将退役"}}
            ]}
            known = self.book("world", "read", "--entity-id", "test_shenyan", "--entity-id", "test_wugang")
            seed["operations"] = [item for item in seed["operations"] if item["entity_id"] in known["missing_ids"]]
            if seed["operations"]:
                proposal = self.book("world", "propose-patch", "--input", "-", stdin=json.dumps(seed, ensure_ascii=False))
                self.book("changes", "apply", proposal["change_set_id"])
            self.report["checks"]["project_and_world_setup"] = True
        save(self.output / "project-initial.json", self.book("project", "show"))
        save(self.output / "world-initial.json", self.book("world", "show", "--category", "character"))
        save(self.output / "world-entity-initial.json", self.book("world", "read", "--entity-id", "test_shenyan"))
        self.book("world", "rebuild")
        self.book("tree", "activate", "--tree-id", self.book("tree", "show")["active_tree_id"])
        self.report["checks"]["initial_tree_activation"] = True
        models = self.cli("models", "status")
        save(self.output / "models.json", models)
        by_id = {item["model_id"]: item for item in models["models"]}
        missing = []
        for stage in ("planning", "agent", "extraction"):
            model = by_id.get(models["routes"][stage]["primary_model_id"], {})
            if not model.get("enabled") or not model.get("provider_enabled") or not (model.get("api_key_configured") or model.get("local_service")):
                missing.append(stage)
        if missing:
            self.report["status"] = "blocked_missing_model_credentials"
            self.report["blocked_stages"] = missing
            self.note("账号、建书、剧情设定、世界书和初始树调整已通过真实 CLI 执行。缺少模型密钥，尚未生成正文。")
            return False
        return True

    def maintain(self, node):
        result = self.book("chapter", "maintain", "--node-id", node["id"])
        save(self.output / f"maintenance-{node['id']}.json", result)
        save(self.output / f"world-{node['id']}.json", self.book("world", "show", "--category", "character", "--limit", "500"))

    def approve_new_chapter(self, ids, number, target):
        found = []
        for change_id in ids:
            preview = self.book("changes", "show", change_id, "--include-content")
            save(self.output / f"proposal-{change_id}.json", preview)
            for operation in preview["change_set"]["operations"]:
                data = operation["payload"]
                if operation["operation"] == "chapter.save_version":
                    require(int(data.get("chapter_num", 0)) == number, "提案章号错误，拒绝应用")
                    require(data.get("parent_id") == target["parent_id"], "提案父节点错误，拒绝应用")
                    require(len(data.get("content", "").strip()) >= 600, "正文过短，拒绝应用")
                    found.append((change_id, operation["operation_id"]))
        require(len(found) == 1, "必须恰好有一份新章提案；不自动重复收费生成")
        change_id, operation_id = found[0]
        self.book("changes", "apply", change_id, "--operation-id", operation_id)
        return self.book("chapter", "current")["node"]

    def adjust(self):
        original = self.book("chapter", "current")
        node = original["node"]
        require(original["page"]["next_start"] is None, "修订原文需要继续分页，拒绝用截断正文修订")
        save(self.output / "tree-before.json", self.book("tree", "show", "--include-summaries"))
        proposal = self.book("chapter", "propose-revision", "--base-node-id", node["id"], "--title", node["title"], "--input", "-",
            "--reason", "保留旧版、验证修订分支", stdin=original["content"].rstrip() + "\n\n沈砚将旧信和抄好的潮位表分别收进两个口袋。")
        applied = self.book("changes", "apply", proposal["change_set_id"])
        revision_id = applied["change_set"]["operations"][0]["payload"]["created_node_id"]
        require(self.book("chapter", "current")["node"]["id"] == node["id"], "修订批准不应自动激活")
        self.book("tree", "activate", "--node-id", revision_id)
        self.maintain(self.book("chapter", "current")["node"])
        summary = self.book("project", "show")["project"]["plot_summary"]
        self.book("tree", "activate", "--node-id", node["id"])
        require(self.book("chapter", "current")["content"] == original["content"], "原分支正文被改变")
        self.book("tree", "activate", "--node-id", revision_id)
        require(self.book("project", "show")["project"]["plot_summary"] == summary, "摘要未随分支恢复")
        save(self.output / "tree-after.json", self.book("tree", "show", "--include-summaries"))
        self.report["checks"]["branch_revision"] = revision_id

    def generate(self):
        self.report["status"] = "running"
        self.report.pop("blocked_stages", None)
        for number, chapter in enumerate(self.scenario["chapters"], 1):
            if number in self.report["completed_chapters"]:
                continue
            pending = self.report.get("pending_chapter", {})
            if pending.get("number") == number and number != 2:
                current = self.book("chapter", "current")["node"]
                require(current["id"] == pending["node_id"], "待维护章节已不在当前末端，停止恢复")
                self.note(f"[{number}/5] 恢复已写入章节的记忆维护")
                self.maintain(current)
                self.report["completed_chapters"].append(number)
                self.report.pop("pending_chapter", None)
                self.checkpoint()
                continue
            target = self.book("tree", "show")["target"]
            require(target["chapter_num"] == number, "活跃路径不符合断点，需检查上次已应用结果，避免重复生成")
            plan_path = self.output / f"plan-{number}.json"
            if plan_path.exists():
                self.note(f"[{number}/5] 复用已保存的 Agent 规划")
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                require(plan["target"] == target, "缓存规划目标发生变化，请重新审查规划")
            else:
                self.note(f"[{number}/5] 通过 CLI 调用真实 Agent 规划")
                plan = self.book("agent", "plan", "--instruction", chapter["plot"], "--chapter-title", chapter["title"], "--target-words", str(self.scenario["target_words"]))
                save(plan_path, plan)
            self.report["plans"][str(number)] = plan["plan"]["plan_id"]
            save(self.output / f"plan-read-{number}.json", self.book("agent", "plan-show", "--plan-id", plan["plan"]["plan_id"]))
            instruction = self.scenario["writing_demand"] + "\n本章剧情：" + chapter["plot"] + "\nAgent 计划：\n" + plan["rendered"] + f"\n只提交第{number}章，父节点为 {target['parent_id']}。"
            instruction += "\n编辑约束优先于计划：沈砚今年29岁，十年前是19岁。末班邮船从主港出发，绕行后最后靠泊隔水的旧码头，寄存柜在旧码头，必须乘船才可接近；不要写已经到了目标寄存柜又无故乘船离开。场景时间和动机必须连贯。"
            generation_path = self.output / f"generation-{number}.json"
            result = json.loads(generation_path.read_text(encoding="utf-8")) if generation_path.exists() else {}
            if not result.get("change_set_ids"):
                self.note(f"[{number}/5] 通过 CLI 生成正文提案")
                result = self.book("write", "chapter", "--input", "-", "--chapter-title", chapter["title"], "--target-words", str(self.scenario["target_words"]), stdin=instruction)
                save(generation_path, result)
            else:
                self.note(f"[{number}/5] 复用已有正文提案，继续检查")
            save(self.output / f"agent-run-{number}.json", self.book("agent", "run-show", "--run-id", result["run_id"]))
            repair_path = self.output / f"reviewed-change-{number}.json"
            change_ids = json.loads(repair_path.read_text(encoding="utf-8"))["change_set_ids"] if repair_path.exists() else result["change_set_ids"]
            node = self.approve_new_chapter(change_ids, number, target)
            self.report["pending_chapter"] = {"number": number, "node_id": node["id"]}
            self.checkpoint()
            self.maintain(node)
            if number == 2:
                self.note("通过 CLI 创建、激活第二章修订分支并往返检查")
                self.adjust()
            self.report["completed_chapters"].append(number)
            self.report.pop("pending_chapter", None)
            self.checkpoint()
        self.export()

    def export(self):
        project = self.book("project", "show")["project"]
        nodes = project["active_path"]
        require([node["chapter_num"] for node in nodes] == [1, 2, 3, 4, 5], "活跃路径不是完整五章")
        require(nodes[2]["parent_id"] == self.report["checks"]["branch_revision"], "第三章未承接修订分支")
        parts = ["# " + self.scenario["title"]]
        count = 0
        for number, node in enumerate(nodes, 1):
            body, start = [], 0
            while True:
                page = self.book("chapter", "read", "--node-id", node["id"], "--start", str(start))
                body.append(page["content"])
                start = page["page"]["next_start"]
                if start is None:
                    break
            content = "".join(body)
            count += len(content)
            parts.append(f"## 第{number}章 {node['title']}\n\n{content}")
        (self.output / "完整小说.md").write_text("\n\n".join(parts), encoding="utf-8")
        save(self.output / "tree-final.json", self.book("tree", "show", "--include-summaries"))
        save(self.output / "world-audit.json", self.book("world", "audit"))
        save(self.output / "world-search.json", self.book("world", "search", "--query", "沈砚"))
        save(self.output / "chapter-search.json", self.book("chapter", "search", "--query", "潮汐", "--scope", "all"))
        overview = self.book("world", "show")
        world = {}
        for category in overview["categories"]:
            world[category], offset = [], 0
            while True:
                page = self.book("world", "show", "--category", category, "--offset", str(offset), "--limit", "500")
                world[category].extend(page["items"])
                offset = page["page"]["next_offset"]
                if offset is None:
                    break
        save(self.output / "world-final.json", world)
        self.report["checks"].update(active_chapters=5, prose_chars=count, all_application_operations_via_cli=True)
        self.report.pop("error", None)
        self.report["status"] = "generated_pending_editorial_review"
        self.note("五章和功能检查完成，等待阅读全文进行文学与连续性验收。")


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/five-chapter-cli-test")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    run = Run(args.output.resolve())
    if args.prepare_only:
        run.checkpoint()
        print("只准备方案，未注册账号或调用模型")
        return 0
    try:
        if run.setup():
            run.generate()
            return 0
        return 3
    except Exception as exc:
        message = str(exc)
        for secret in run.sensitive:
            if secret:
                message = message.replace(secret, "[REDACTED]")
        run.report.update(status="failed", error=message)
        run.checkpoint()
        print(message, file=sys.stderr)
        return 1
    finally:
        run.env.pop("DSA_TEST_PASSWORD", None)
        run.env.pop("DEEPSEEKASS_CONTROL_TOKEN", None)


if __name__ == "__main__":
    raise SystemExit(main())
