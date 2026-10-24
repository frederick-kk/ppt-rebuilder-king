"""Opt-in, standard-library-only inspection of already recorded job evidence.

This module never imports the reconstruction runtime. Exit 0 means a diagnostic
report was written, never that the presentation passed visual acceptance.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys


RUNTIME = Path(__file__).resolve().parents[1] / "compat-runtime"
UNKNOWN = "未记录"
WARNING_EXPLANATIONS = {
    "source_matte_collision_unresolved": "源图主体颜色与抠图底色冲突尚未解决，可能误删主体颜色；需要人工核对源图和提取素材。",
    "touch_slot_edge": "主体触及槽位边缘，存在被裁切的风险；此警告本身不证明已裁切。",
    "safe_area_cross": "主体越过预留安全区域，可能减少边距或发生裁切；需要核对实际素材。",
    "cross_slot": "主体在相邻槽位方向存在连续像素，可能跨槽或与邻项融合；不能由此认定素材已独立完整。",
    "ambiguous_member_omitted": "成员与槽位的对应关系有歧义，运行时已省略该成员；并未自动补造替代图。",
}
PHASE_EXPLANATIONS = {
    "layers": "图层阶段；waiting/issued 只记录任务调度状态，不证明图像已生成。",
    "describe": "等待或已进入页面描述阶段；描述记录不等于视觉验收。",
    "boards": "素材板任务阶段；读取已记录的任务与提取信息，不推进队列。",
    "done": "控制器记录为终态；done 表示文件交付流程结束，quality 仍以原始记录为准。",
}
STATUS_EXPLANATIONS = {
    "waiting": "等待结果登记；issued=true 仅表示已下发。",
    "recorded": "已有结果文件登记；不等于内容正确或视觉验收通过。",
    "failed": "调用方或登记环节记录失败；检查原始 failure/attempts 记录。",
}


def _inside(path, root):
    return path == root or root in path.parents


def _link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _inventory(job):
    """Hash regular files without traversing symbolic links or junctions."""
    inventory = {}

    def visit(directory):
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = path.relative_to(job).as_posix()
            if _link(path):
                inventory[relative] = {"kind": "link", "followed": False}
            elif path.is_dir():
                inventory[relative + "/"] = {"kind": "directory"}
                visit(path)
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                info = path.stat()
                inventory[relative] = {"kind": "file", "sha256": digest.hexdigest(),
                                       "size": info.st_size, "mtime_ns": info.st_mtime_ns}
            else:
                raise ValueError("Refusing special source file: " + relative)

    visit(job)
    return inventory


def _read_record(job, relative):
    path = job / relative
    # Do not let a saved report link make the inspector read outside the job.
    for candidate in (path, *path.parents):
        if candidate == job:
            break
        if os.path.lexists(candidate) and _link(candidate):
            return {"path": relative, "status": "unsafe", "detail": "链接或联接未读取"}, None
    if not path.exists():
        return {"path": relative, "status": "missing", "detail": UNKNOWN}, None
    if not path.is_file():
        return {"path": relative, "status": "invalid", "detail": "应为 JSON 文件"}, None
    try:
        def reject_constant(value):
            raise ValueError("Non-finite JSON number")
        value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
        if not isinstance(value, dict):
            return {"path": relative, "status": "invalid", "detail": "顶层应为 JSON 对象"}, None
        return {"path": relative, "status": "recorded"}, value
    except (ValueError, UnicodeError) as error:
        detail = "JSON 无法解析"
        if isinstance(error, json.JSONDecodeError):
            detail += " (line {}, column {})".format(error.lineno, error.colno)
        return {"path": relative, "status": "invalid", "detail": detail}, None
    except OSError as error:
        return {"path": relative, "status": "unreadable", "detail": type(error).__name__}, None


def _field(record, key, kind, source, issues):
    value = record.get(key)
    if value is None:
        issues.append({"source": source + "." + key, "code": "not_recorded", "detail": UNKNOWN})
        return None
    if not isinstance(value, kind):
        issues.append({"source": source + "." + key, "code": "invalid_shape", "detail": "记录类型不符合基线结构"})
        return None
    return value


def _warning(record, source):
    code = record.get("code") if isinstance(record, dict) else None
    code = code if isinstance(code, str) else None
    return {"source": source, "code": code, "severity": "risk",
            "explanation": WARNING_EXPLANATIONS.get(code, "原始风险提示；未重新判断成败，需要结合原始记录核对。"),
            "record": record}


def _measurements(objects, issues):
    entries, overflow, fallback_overflow = [], [], []
    if objects is not None:
        for index, item in enumerate(objects):
            if not isinstance(item, dict):
                issues.append({"source": "compiled_report.objects[{}]".format(index), "code": "invalid_shape", "detail": "对象应为 JSON 对象"})
                continue
            candidates = [(item, {"id": item.get("id"), "kind": item.get("kind")})]
            cells = item.get("cell_lines", [])
            if not isinstance(cells, list):
                issues.append({"source": "compiled_report.objects[{}].cell_lines".format(index), "code": "invalid_shape", "detail": "单元格文字行应为列表"})
                cells = []
            for cell in cells:
                if isinstance(cell, dict):
                    candidates.append((cell, {"id": item.get("id"), "kind": "table_cell_line",
                                              **{key: cell.get(key) for key in ("row", "column", "line")}}))
                else:
                    issues.append({"source": "compiled_report.objects[{}].cell_lines".format(index), "code": "invalid_shape", "detail": "单元格文字行应为对象"})
            for candidate, identity in candidates:
                measurement = candidate.get("measurement")
                if measurement is None:
                    if candidate.get("kind") in ("text", "formula") or identity["kind"] == "table_cell_line":
                        issues.append({"source": "compiled_report.measurement", "code": "not_recorded", "detail": "{} 的文字测量未记录".format(identity["id"])})
                    continue
                if not isinstance(measurement, dict):
                    issues.append({"source": "compiled_report.measurement", "code": "invalid_shape", "detail": "文字测量应为对象"})
                    continue
                entry = {**identity, "measurement": measurement,
                         "authored_size_px": candidate.get("authored_size_px"),
                         "effective_size_px": candidate.get("effective_size_px")}
                entries.append(entry)
                if not isinstance(measurement.get("status"), str):
                    issues.append({"source": "compiled_report.measurement.status", "code": "invalid_shape", "detail": "测量状态未记录或类型错误"})
                remaining = measurement.get("remaining_overflow_px")
                if type(remaining) in (int, float) and math.isfinite(remaining) and remaining > 0:
                    overflow.append({**identity, "status": measurement.get("status"), "remaining_overflow_px": remaining})
                fallback = measurement.get("literal_fallback")
                if isinstance(fallback, dict):
                    remaining = fallback.get("remaining_overflow_px")
                    if type(remaining) in (int, float) and math.isfinite(remaining) and remaining > 0:
                        fallback_overflow.append({**identity, "status": fallback.get("status"), "remaining_overflow_px": remaining})
    return {"entries": entries, "status_counts": dict(Counter(entry["measurement"]["status"] if isinstance(entry["measurement"].get("status"), str) else "not_recorded" for entry in entries)),
            "known_overflow": overflow, "literal_fallback_overflow": fallback_overflow,
            "explanation": "measured 是已记录的字体度量；estimated 是估算，不能证明不溢出。remaining_overflow_px 保留已知宽度超出；原生 Office 数学公式几何未测量，literal_fallback 仅为兼容文字宽度。没有测量记录时为未记录。"}


def build_report(job):
    """Inspect a stable existing job and return honest recorded evidence."""
    job = Path(job).absolute()
    if not job.is_dir() or _link(job):
        raise ValueError("Refusing missing or linked job directory")
    job = job.resolve()
    before = _inventory(job)
    records, documents, issues = {}, {}, []
    for name, relative in (("state", "state.json"), ("receipt", "receipt.json"),
                           ("compiled_report", "build/compiled.report.json")):
        records[name], documents[name] = _read_record(job, relative)
        if records[name]["status"] != "recorded":
            issues.append({"source": relative, "code": records[name]["status"], "detail": records[name]["detail"]})
    state = documents["state"] or {}
    receipt = documents["receipt"] or {}
    compiled = documents["compiled_report"] or {}
    warnings, skipped, tasks = [], [], []
    raw_tasks = _field(state, "tasks", dict, "state", issues)
    if raw_tasks is not None:
        for identity, task in raw_tasks.items():
            if not isinstance(task, dict):
                issues.append({"source": "state.tasks." + identity, "code": "invalid_shape", "detail": "任务应为对象"})
                continue
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            attempts = task.get("attempts")
            if attempts is not None and not isinstance(attempts, list):
                issues.append({"source": "state.tasks." + identity + ".attempts", "code": "invalid_shape", "detail": "尝试记录应为列表"})
            elif isinstance(attempts, list):
                for index, attempt_record in enumerate(attempts):
                    if not isinstance(attempt_record, dict):
                        issues.append({"source": "state.tasks." + identity + ".attempts[{}]".format(index),
                                       "code": "invalid_shape", "detail": "单次尝试记录应为对象"})
            task_record = {key: task.get(key) for key in ("id", "role", "status", "attempt", "issued", "processed", "failure", "extraction")}
            task_record["id"] = task_record["id"] or identity
            for key in ("role", "status", "attempt", "issued"):
                value = task.get(key)
                valid = (isinstance(value, str) if key in ("role", "status") else
                         type(value) is int and value >= 0 if key == "attempt" else type(value) is bool)
                if not valid:
                    issues.append({"source": "state.tasks." + identity + "." + key,
                                   "code": "not_recorded" if value is None else "invalid_shape", "detail": "任务字段未记录或类型错误"})
            task_record.update(attempts=attempts, invocation_count=result.get("invocation_count"),
                               tool=result.get("tool"), tool_call_id=result.get("tool_call_id"), tool_output_id=result.get("tool_output_id"),
                               status_explanation=STATUS_EXPLANATIONS.get(task.get("status"), UNKNOWN) if isinstance(task.get("status"), str) else UNKNOWN)
            tasks.append(task_record)
            extraction = task.get("extraction")
            if isinstance(extraction, dict):
                mapping = extraction.get("cell_mapping")
                if isinstance(mapping, dict) and isinstance(mapping.get("warnings"), list):
                    warnings.extend(_warning(value, "state.tasks." + identity + ".extraction.cell_mapping") for value in mapping["warnings"])
    for source, document in (("state", state), ("receipt", receipt), ("compiled_report", compiled)):
        for key, destination in (("warnings", warnings), ("skipped", skipped)):
            value = document.get(key)
            if value is not None:
                if isinstance(value, list):
                    destination.extend(_warning(item, source) if key == "warnings" else {"source": source, "record": item} for item in value)
                else:
                    issues.append({"source": source + "." + key, "code": "invalid_shape", "detail": "应为列表"})
    objects = _field(compiled, "objects", list, "compiled_report", issues)
    counts = _field(compiled, "native_counts", dict, "compiled_report", issues)
    equations = _field(compiled, "equations", dict, "compiled_report", issues)
    validation = _field(compiled, "validation", dict, "compiled_report", issues)
    if counts is not None:
        for key in ("text_frames", "shapes", "lines", "table_groups", "table_cells", "foreground_images"):
            value = counts.get(key)
            if type(value) is not int or value < 0:
                issues.append({"source": "compiled_report.native_counts." + key,
                               "code": "not_recorded" if value is None else "invalid_shape", "detail": "原生对象数量未记录或不是非负整数"})
    if equations is not None:
        for key in ("embedded", "fallback", "warnings"):
            _field(equations, key, list, "compiled_report.equations", issues)
    if validation is not None:
        for key in ("zip_valid", "xml_well_formed", "background_picture_fill", "office_render_checked"):
            value = validation.get(key)
            if type(value) is not bool:
                issues.append({"source": "compiled_report.validation." + key,
                               "code": "not_recorded" if value is None else "invalid_shape", "detail": "检查结果未记录或不是布尔值"})
        _field(validation, "required_parts", list, "compiled_report.validation", issues)
    measurement = _measurements(objects, issues)
    done = state.get("done")
    if done is not None and not isinstance(done, dict):
        issues.append({"source": "state.done", "code": "invalid_shape", "detail": "终态记录应为对象；原始数据保留"})
    assets = state.get("assets")
    if assets is not None and not isinstance(assets, list):
        issues.append({"source": "state.assets", "code": "invalid_shape", "detail": "素材记录应为列表；原始数据保留"})
    elif isinstance(assets, list):
        for index, asset in enumerate(assets):
            if not isinstance(asset, dict):
                issues.append({"source": "state.assets[{}]".format(index), "code": "invalid_shape", "detail": "单项素材记录应为对象；原始数据保留"})
    done_receipt = done if isinstance(done, dict) else {}
    quality = receipt.get("quality", done_receipt.get("quality"))
    phase = state.get("phase")
    if not isinstance(phase, str):
        issues.append({"source": "state.phase", "code": "not_recorded" if phase is None else "invalid_shape", "detail": "阶段未记录或类型错误"})
    if quality is None:
        issues.append({"source": "receipt.quality", "code": "not_recorded", "detail": UNKNOWN})
    links = [path for path, entry in before.items() if entry.get("kind") == "link"]
    if links:
        issues.append({"source": "job", "code": "links_not_followed", "detail": "链接或联接未读取", "paths": links})
    after = _inventory(job)
    if before != after:
        raise ValueError("Refusing report: source job changed during inspection")
    return {"format": "ppt-rebuilder.inspection/1", "job_root": str(job), "read_only": True,
            "records": records, "data_issues": issues,
            "assessment": {"evidence_status": "incomplete" if issues else "recorded",
                           "visual_acceptance": None,
                           "explanation": "只解释现有记录；缺失、损坏或未记录的数据不视为通过。风险警告不自动判定失败，done 不代表视觉验收通过，quality:not_evaluated 表示质量未评估。"},
            "workflow": {"phase": phase, "phase_explanation": PHASE_EXPLANATIONS.get(phase, UNKNOWN) if isinstance(phase, str) else UNKNOWN,
                         "tasks": tasks, "task_count": len(tasks) if raw_tasks is not None else None,
                         "done": done, "receipt": documents["receipt"], "quality": quality,
                         "caller_reported_invocations": receipt.get("caller_reported_invocations", done_receipt.get("caller_reported_invocations")),
                         "invocation_explanation": "调用次数和工具身份仅沿用调用方上报；未记录保持 null，不从任务数、attempt 或 issued 推断实际调用、服务身份或耗时。attempt 是任务重开序号，不等于调用次数。"},
            "extraction": {"assets": assets, "explanation": "沿用已记录的素材与任务 extraction.cell_mapping；不重新提取、生成或验收图像。"},
            "warnings": warnings, "skipped": skipped,
            "rendering": {"objects": objects, "native_counts": counts, "equations": equations,
                          "validation": validation, "text_measurement": measurement,
                          "validation_explanation": "ZIP/XML 结构正常只证明容器检查记录；office_render_checked:false 表示未进行 Office 实际渲染验收。字段缺失为未记录，不视为通过。原生对象数量沿用报告，不重新构建或计数。"},
            "source_integrity": {"unchanged": True, "before": before, "after": after,
                                 "explanation": "检查前后文件 SHA256、大小与修改时间清单一致；此结论仅覆盖本次检查区间，未跟随链接。"}}


def render_markdown(report):
    def shown(value):
        return UNKNOWN if value is None else json.dumps(value, ensure_ascii=False)

    workflow = report["workflow"]
    lines = ["# PPT Rebuilder King 作业只读检查", "", report["assessment"]["explanation"], "",
             "- 证据状态：" + report["assessment"]["evidence_status"],
             "- phase：" + shown(workflow["phase"]) + "；" + workflow["phase_explanation"],
             "- quality：" + shown(workflow["quality"]),
             "- done：" + shown(workflow["done"]),
             "- caller_reported_invocations：" + shown(workflow["caller_reported_invocations"]),
             "", workflow["invocation_explanation"], "", "## 任务", ""]
    if not workflow["tasks"]:
        lines.append("任务列表：" + (UNKNOWN if workflow["task_count"] is None else "记录为空列表"))
    for task in workflow["tasks"]:
        lines.append("- {} / {}：status={}，attempt={}，issued={}。{}".format(task["id"], shown(task["role"]), shown(task["status"]), shown(task["attempt"]), shown(task["issued"]), task["status_explanation"]))
    lines += ["", "## 警告与省略", ""]
    if not report["warnings"]:
        lines.append("警告条目：无可读取条目；不据此宣称无风险。")
    for warning in report["warnings"]:
        lines.append("- {}：{} 原始记录：{}".format(warning["code"] or warning["source"], warning["explanation"], shown(warning["record"])))
    for skipped in report["skipped"]:
        lines.append("- skipped（{}）：{}".format(skipped["source"], shown(skipped["record"])))
    rendering = report["rendering"]
    measurement = rendering["text_measurement"]
    lines += ["", "## 原生对象、文字与检查", "", rendering["validation_explanation"], "", measurement["explanation"], "",
              "- native_counts：" + shown(rendering["native_counts"]), "- equations：" + shown(rendering["equations"]),
              "- validation：" + shown(rendering["validation"]), "- 测量状态：" + shown(measurement["status_counts"]),
              "- 已知宽度超出：" + shown(measurement["known_overflow"]),
              "- 公式兼容文字宽度超出（不代表公式几何）：" + shown(measurement["literal_fallback_overflow"]),
              "", "## 缺失或异常记录", ""]
    lines += ["- {}：{}".format(issue["source"], issue["detail"]) for issue in report["data_issues"]]
    lines += ["", "## 完整诊断数据", "", "包含原始对象、表格文字行、任务提取元数据和检查前后哈希清单。", "", "```json", json.dumps(report, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def inspect_job(job, output, format="markdown"):
    """Write a new report outside the job and immutable runtime."""
    if format not in ("json", "markdown"):
        raise ValueError("Unsupported report format")
    original_job = Path(job).absolute()
    if not original_job.is_dir() or _link(original_job):
        raise ValueError("Refusing missing or linked job directory")
    job = original_job.resolve()
    output_path = Path(output).absolute()
    if os.path.lexists(output_path):
        raise ValueError("Refusing existing output")
    destination = output_path.resolve()
    if _inside(destination, job) or _inside(destination, RUNTIME.resolve()):
        raise ValueError("Refusing output inside source job or immutable runtime")
    report = build_report(job)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n" if format == "json" else render_markdown(report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is the final overwrite guard, including concurrent writers.
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read existing job records only. Exit 0 reports writing success, not quality acceptance.")
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    arguments = parser.parse_args(argv)
    try:
        inspect_job(arguments.job, arguments.output, arguments.format)
    except (ValueError, OSError) as error:
        print("Inspector: " + str(error), file=sys.stderr)
        return 2
    print("Inspection report written; no visual acceptance is implied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
