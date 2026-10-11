"""Render a shareable field-level replay of the verified q31/q68/q92 cache."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import sys
from typing import Any


EXPECTED_PACKETS = ("q31", "q68", "q92")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _verified_inputs(results_path: Path, manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _need(manifest.get("schema_version") == "steptrace-cloud-artifact-manifest/1", "artifact manifest schema mismatch")
    _need(results.get("schema_version") == "process-visible-state-offline-control/1", "cache replay result schema mismatch")
    source = manifest["source_cache"]
    _need(results.get("cache_source_commit") == source.get("commit"), "cache source commit mismatch")
    _need(results.get("cache_manifest_sha256") == source.get("public_manifest_sha256"), "cache manifest hash mismatch")

    project_root = results_path.resolve().parents[2]
    artifact_root_value = manifest.get("artifact_root")
    _need(isinstance(artifact_root_value, str) and bool(artifact_root_value.strip()), "artifact_root is missing")
    asset_root = (project_root / artifact_root_value).resolve()
    _need(results_path.resolve().is_relative_to(asset_root), "offline replay results are outside their declared artifact root")
    result_relative = results_path.resolve().relative_to(asset_root).as_posix()
    result_record = next(
        (row for row in manifest.get("files", []) if row.get("path") == result_relative),
        None,
    )
    _need(isinstance(result_record, dict), "artifact manifest omits offline replay results")
    _need(
        _sha256(results_path) == result_record.get("sha256"),
        "offline results SHA-256 does not match artifact manifest",
    )
    public_manifest_path = project_root / source["public_manifest_path"]
    _need(public_manifest_path.is_file(), "pinned public cache manifest is missing")
    manifest_hash = _sha256(public_manifest_path)
    _need(manifest_hash == source["public_manifest_sha256"], "pinned public cache manifest SHA-256 mismatch")

    expected_answers = {Path(row["path"]).name.removesuffix(".json"): row["sha256"] for row in source["cached_answer_json"]}
    for packet_id in EXPECTED_PACKETS:
        _need(packet_id in expected_answers, f"source manifest omits {packet_id}")
        row = results.get("records", {}).get(packet_id)
        _need(isinstance(row, dict), f"offline replay omits {packet_id}")
        _need(row.get("source_record_sha256") == expected_answers[packet_id], f"{packet_id} source record hash mismatch")
        answer_path = project_root / "experiments/real-reappearance-dev-20261010/model_outputs" / f"{packet_id}.json"
        _need(answer_path.is_file(), f"cached answer file missing: {packet_id}")
        _need(_sha256(answer_path) == expected_answers[packet_id], f"cached answer file SHA-256 mismatch: {packet_id}")
    return results, manifest, manifest_hash


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _relation_label(value: str) -> str:
    return {
        "external_connection_supported": "缓存记录：当前直接支持外部连接",
        "occluded_or_unknown": "当前直接关系：unknown（接口遮挡）",
        "disconnected": "缓存记录：当前直接分离",
    }.get(value, value)


def render_comparison(results_path: Path, manifest_path: Path) -> str:
    results, source_manifest, verified_manifest_hash = _verified_inputs(results_path, manifest_path)
    records = results["records"]

    cards = []
    for packet_id in EXPECTED_PACKETS:
        record = records[packet_id]
        state = record["current_visible_state"]
        process = record["process_evidence"]
        current = state["direct_relation_by_record"]
        basis = state["estimate_basis"]
        applicability = state["claim_applicability"]
        history = state.get("historical_connections", [])
        frames = record.get("frame_history", [])
        timeline = " → ".join(
            f"<span>{_escape(frame['frame_id'])}: {_escape(frame['relation_state'])}</span>"
            for frame in frames
        )
        assumptions = state.get("assumptions", [])
        if packet_id == "q68":
            interpretation = (
                "f1743 的历史连接记录被保留；f1746 当前关系仍是 unknown。"
                "沿用连接只在原答复列出的身份连续、期间未分离等条件成立时适用。"
            )
            note_class = "conditional"
        else:
            interpretation = "仅表示该缓存答复对所列帧作出的字段判断，不是人工或物理真值。"
            note_class = ""
        assumptions_html = "".join(f"<li>{_escape(item)}</li>" for item in assumptions)
        assumptions_section = (
            f'  <ul class="assumptions">{assumptions_html}</ul>\n'
            if assumptions_html
            else ""
        )
        history_id = ", ".join(_escape(item.get("connection_id", "")) for item in history) or "无"
        cards.append(
            f"""<article class="record {note_class}" aria-labelledby="{_escape(packet_id)}-title">
  <h2 id="{_escape(packet_id)}-title">{_escape(packet_id)} · {_escape(state['frame_id'])}</h2>
  <p class="relation">{_escape(_relation_label(current))}</p>
  <dl>
    <div><dt>状态依据</dt><dd><code>{_escape(basis)}</code></dd></div>
    <div><dt>历史适用性</dt><dd><code>{_escape(applicability)}</code></dd></div>
    <div><dt>历史记录</dt><dd>{history_id}</dd></div>
    <div><dt>步骤完成</dt><dd><strong>{_escape(process['step_completion'])}</strong></dd></div>
    <div><dt>过程输入</dt><dd><code>{_escape(process['input_scope'])}</code></dd></div>
  </dl>
  <p class="path-label">缓存所含帧关系</p>
  <div class="frame-path" aria-label="{_escape(packet_id)} 帧关系路径">{timeline}</div>
{assumptions_section}  <p class="interpretation">{_escape(interpretation)}</p>
  <p class="record-hash">答复 SHA-256 <code>{_escape(record['source_record_sha256'])}</code></p>
</article>"""
        )

    source_commit = source_manifest["source_cache"]["commit"]
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="StepTrace q31/q68/q92 已核验缓存字段回放">
  <title>StepTrace q31 · q68 · q92 缓存字段回放</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
    body {{ margin: 0; padding: 1.25rem; color: CanvasText; background: Canvas; }}
    main {{ max-width: 68rem; margin: 0 auto; }}
    h1 {{ margin: 0 0 .35rem; font-size: clamp(1.35rem, 3vw, 1.9rem); }}
    .subtitle, .scope, .provenance {{ color: color-mix(in srgb, CanvasText 72%, Canvas); }}
    .scope {{ margin: 1rem 0; padding: .75rem 1rem; border-left: .25rem solid #b7791f; background: color-mix(in srgb, #b7791f 12%, Canvas); }}
    .records {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; align-items: stretch; }}
    .record {{ min-width: 0; padding: 1rem; border: 1px solid color-mix(in srgb, CanvasText 18%, Canvas); border-radius: .6rem; }}
    .record.conditional {{ border-color: #b7791f; box-shadow: inset 0 .25rem #b7791f; }}
    h2 {{ margin: 0 0 .6rem; font-size: 1.15rem; }}
    .relation {{ min-height: 2.8rem; font-weight: 650; }}
    dl {{ margin: .6rem 0; }}
    dl div {{ display: grid; grid-template-columns: 6rem minmax(0, 1fr); gap: .5rem; padding: .22rem 0; }}
    dt {{ color: color-mix(in srgb, CanvasText 68%, Canvas); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    .path-label {{ margin: 1rem 0 .4rem; font-size: .9rem; font-weight: 600; }}
    .frame-path {{ display: flex; flex-wrap: wrap; align-items: center; gap: .35rem; font-size: .82rem; }}
    .frame-path span {{ padding: .28rem .42rem; border-radius: .3rem; background: color-mix(in srgb, #3178a8 14%, Canvas); }}
    .assumptions {{ padding-left: 1.2rem; font-size: .88rem; }}
    .interpretation {{ min-height: 3.2rem; font-size: .9rem; }}
    .record-hash {{ margin: 1rem 0 0; font-size: .75rem; overflow-wrap: anywhere; }}
    .q68-callout {{ margin: 1rem 0; padding: .8rem 1rem; border-left: .25rem solid #b7791f; }}
    .timeline-labels {{ display: flex; justify-content: space-between; color: color-mix(in srgb, CanvasText 65%, Canvas); font-size: .78rem; }}
    .provenance {{ margin-top: 1.2rem; font-size: .78rem; overflow-wrap: anywhere; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    @media (max-width: 760px) {{ .records {{ grid-template-columns: 1fr; }} .record {{ padding: .85rem; }} .relation, .interpretation {{ min-height: 0; }} }}
  </style>
</head>
<body>
<main>
  <h1>q31 → q68 → q92：缓存字段回放</h1>
  <p class="subtitle">同一开发事件的三个相关输入前缀。字段并排展示当前关系、历史条件与步骤完成。</p>
  <p class="scope"><strong>范围：</strong>缓存字段回放，不是新模型实验。云端没有这些记录对应的原始图像；本页不生成或模拟原图。缓存标签不是人工、像素或物理真值。</p>
  <div class="timeline-labels" aria-label="三个相关前缀"><span>q31 / f1743</span><span>q68 / f1746</span><span>q92 / f1752</span></div>
  <section class="records" aria-label="缓存记录逐条对比">{''.join(cards)}</section>
  <aside class="q68-callout"><strong>关键边界：</strong>q68 只记录了 f1743 的历史连接，并把 f1746 的当前直接关系记为 unknown；历史连续性是假设条件，不能直接写成“当前已连接”。q92 后来再次出现直接支持，也不会回填 q68 当时不可见的关系。</aside>
  <p class="scope"><strong>步骤判断：</strong>三份记录都标为 <code>unknown</code>。缓存记有 <code>missedprocess</code>（输入未提供安装过程），端点外观不能替代 SOP 过程证据。</p>
  <footer class="provenance">
    来源提交：<code>{_escape(source_commit)}</code><br>
    公开缓存 manifest SHA-256：<code>{_escape(verified_manifest_hash)}</code><br>
    字段回放结果 SHA-256：<code>{_escape(_sha256(results_path))}</code><br>
    私有素材：未包含在本页；云端缓存路径不代表已回存 Mac。
  </footer>
</main>
</body>
</html>
"""
    return html_doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a verified q31/q68/q92 cache-field comparison")
    parser.add_argument("--results", type=Path, default=Path("experiments/process-visible-state-dev-20261011/results.json"))
    parser.add_argument("--manifest", type=Path, default=Path("experiments/process-visible-state-dev-20261011/MANIFEST.json"))
    parser.add_argument("--output", type=Path, default=Path("experiments/real-validation-protocol-dev-20261011/cache-comparison.html"))
    args = parser.parse_args(argv)
    try:
        page = render_comparison(args.results, args.manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(page, encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "sha256": _sha256(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
