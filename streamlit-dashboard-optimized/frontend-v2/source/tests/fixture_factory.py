#!/usr/bin/env python3
"""Internal deterministic pytest fixture builder, with no product or CLI entry.

conftest must supply a pytest-owned temporary ROOT. Generated values and names
are synthetic; no generated data is shipped with the application.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = None  # Assigned to a pytest-owned temporary directory by conftest.py.
MARKER = ".synthetic-demo.json"
GENERATOR_ID = "optimized-dashboard-synthetic-demo-v1"
EXPERIMENTS = {"baseline-v1": 0.0, "candidate-v2": 0.035, "ablation": -0.045}
CHECKPOINTS = (20000, 40000, 60000)
DATASETS = ("follow", "merge_in", "ugm", "light", "lane_change2", "cgm")
COG_TASKS = ("lane", "lane direction", "lane type", "boundary", "road", "curb", "intersection")
PV_CLASSES = (
    "ground", "vehicle", "pedestrian and rider", "isolation zone on the road",
    "sidewalk", "other obstacles", "non-motor vehicle and motorcycle", "sky",
    "vegetation", "lane line", "zebra crossing", "road marking", "stopline",
    "ego vehicle", "whole curb", "road boundary", "divergence", "deceleration", "parking slot",
)
GOD_RANGES = (
    "LITE_GOD(115.2m*44.8m)", "VISIBLE_LITE_GOD(115.2m*44.8m)",
    "LITE_GOD(115.2m*25.6m)", "VISIBLE_LITE_GOD(115.2m*25.6m)",
    "L3(140.8m*89.6m)", "RAEB(5m*7m)", "RAEB(11m*18m)",
)
IOU_OPTIONS = (
    "IoU@0.5 PedDist@1.0", "IoU@0.5 PedDist@1.5", "IoU@0.7 PedDist@0.5",
    "IoU@0.4 PedDist@1.25", "IoU@0.3 PedDist@1.5",
)


def rounded(value):
    return round(float(value), 5)


def quality(offset, iteration):
    return 0.76 + offset + (iteration / 20000 - 1) * 0.018


def pr_tuple(score):
    """Legacy GOD order: IoU, precision, recall, F1; all are fractions."""
    p, r = min(score + 0.025, 0.99), max(score - 0.015, 0.01)
    f1 = 2 * p * r / (p + r)
    return [rounded(p * r / (p + r - p * r)), rounded(p), rounded(r), rounded(f1)]


def bev_payload(score):
    result = {}
    distances = [90, 60, 30, 150]
    for category_index, category in enumerate(("CAR", "TRUCK", "PEDESTRIAN", "CYCLIST", "HEAD", "CONETANK")):
        result[category] = {}
        for option_index, option in enumerate(IOU_OPTIONS):
            levels = [score - category_index * 0.017 - option_index * 0.004 - distance / 2000 for distance in distances]
            precision = [f"{round(level * 1000)}/1000" for level in levels]
            recall = [f"{round((level - 0.03) * 1100)}/1100" for level in levels]
            velocity = [rounded((1 - level) * 1.8) for level in levels]
            dynamics = []
            for level in levels:
                tp = round(level * 900)
                fp, fn = 70, 90
                dynamics.append(f"{tp / (tp + fp):.6f},{tp / (tp + fn):.6f},{tp},{fp},{fn}")
            result[category][option] = {
                "dist_thres": distances,
                "ap": {"I_BEV": [rounded(level * 100) for level in levels]},
                "precision": {"I_BEV": precision},
                "recall": {"I_BEV": recall},
                "thr_pre": {"I_BEV": [[rounded(level - 0.01), rounded(level), rounded(level + 0.01)] for level in levels]},
                "velocity_error": {"I_BEV": velocity},
                "velocity_degree": {"I_BEV": [rounded((1 - level) * 12) for level in levels]},
                "velocity_error_mean": {"I_BEV": [
                    {"v<=1.5m/s": rounded(error * 0.6), "1.5m/s<v<=8m/s": rounded(error), "v>8m/s": rounded(error * 1.5)}
                    for error in velocity
                ]},
                "moving_pr": {"I_BEV": dynamics},
                "stationary_pr": {"I_BEV": dynamics},
                "mov_ap": {"I_BEV": [rounded((level - 0.02) * 100) for level in levels]},
                "heading_degree": {"I_BEV": [rounded((1 - level) * 9) for level in levels]},
                "corner_diffs": {"I_BEV": [rounded((1 - level) * 0.8) for level in levels]},
            }
    return result


def god_payload(score):
    result = {}
    categories = ("All", "OTHERS", "ROAD", "BUSH", "DYNAMIC", "CURB", "UNOCCUPANCY", "ROOF", "Visible_area(value=1)")
    for range_index, region in enumerate(GOD_RANGES):
        level = score - range_index * 0.003
        result[region] = {
            metric: {category: pr_tuple(level - index * 0.006) for index, category in enumerate(categories)}
            for metric in ("god_occupancy", "god_semantic_all", "god_semantic_thrs", "god_visibility")
        }
        result[region]["god_flow_ve"] = {
            category: {"mean": rounded((1 - level) * scale), "std": rounded(0.08 * scale), "1-Sigma[65dot26%]": rounded(0.1 * scale)}
            for category, scale in (("Static(<=0.3m/s)", 0.5), ("Slow(<=5m/s)", 1.2), ("Fast(>5m/s)", 2.0))
        }
        result[region]["god_flow_di"] = {
            category: [rounded((1 - level) * scale), rounded(scale * 0.1), rounded(scale * 0.12)]
            for category, scale in (("abs_direction<=10", 8), ("abs_direction>10", 15))
        }
        result[region]["god_SD_pr"] = {
            category: {"precision": rounded(level), "recall": rounded(level - 0.02), "f1": pr_tuple(level)[3]}
            for category in ("static", "dynamic")
        }
        result[region]["god_drivable"] = {
            category: pr_tuple(level) for category in ("Drivable_space(value=1)", "Drivable_boundary(width=1.6m)")
        }
    boundary_ranges = (
        "RANGE_0(115.2m*44.8m)", "RANGE_1(0-30m)", "RANGE_2(30-60m)", "RANGE_3(60-95.4m)",
        "RANGE_0(115.2m*12m)", "RANGE_1(0-30m*12m)", "RANGE_2(30-60m*12m)", "RANGE_3(60-95.4m*12m)",
    )
    for metric in ("drivable_boundary", "drivable_boundary_inner", "drivable_boundary_outer"):
        result[metric] = {region: [rounded((1 - score) * 0.3), [0.04, 0.07], 97.5] for region in boundary_ranges}
    return result


def family_payloads(score):
    errors = 1 - score
    cog = {
        task: {
            category: dict(zip(("IoU", "precision", "Recall", "F1"), pr_tuple(score - index * 0.015)))
            for index, category in enumerate(("all", "near", "far"))
        } for task in COG_TASKS
    }
    pnc = {
        dataset: {
            "FDE_30": rounded(errors * 5 + index * 0.05), "FDE_50": rounded(errors * 8 + index * 0.08),
            "ADE_30": rounded(errors * 2 + index * 0.02), "ADE_50": rounded(errors * 3 + index * 0.03),
        } for index, dataset in enumerate(DATASETS)
    }
    navi = {
        metric: {category: {"avg": rounded(errors * scale + index * 0.01), "count": 1200 - index * 200}
                 for index, category in enumerate(("all", "straight", "turn"))}
        for metric, scale in (("ade_in_m", 3.2), ("map_loss", 0.8))
    }
    depth = {
        region: {
            "mae(m)": rounded(errors * 3.4 * factor), "abs_rel(%)": rounded(errors * 35 * factor),
            "abs_rel_25(m)": rounded(errors * 0.8 * factor), "abs_rel_50(m)": rounded(errors * 1.7 * factor),
            "abs_rel_75(m)": rounded(errors * 2.8 * factor), "a1(%)": rounded(score * 100 / factor),
            "a2(%)": rounded(min(99.9, score * 108 / factor)), "a3(%)": rounded(min(99.95, score * 118 / factor)),
            "avg_samples(px)": 184200,
        } for region, factor in (("overall", 1.0), ("front", 0.97), ("surround", 1.03), ("fisheye", 1.08))
    }
    pv = {"cls": list(PV_CLASSES), "mIoU": rounded(score - 0.05), "mPrecision": rounded(score + 0.02), "mRecall": rounded(score)}
    for name, shift in (("iou", -0.05), ("precision", 0.02), ("recall", 0)):
        pv[name] = [rounded(score + shift - index * 0.004) for index in range(len(PV_CLASSES))]
    for camera in (1, 2, 4, 5, 6, 7, 8):
        pv[f"cam_{camera}"] = {key: rounded(pv[key] - camera * 0.002) for key in ("mIoU", "mPrecision", "mRecall")}
    obstacle_classes = ("ALL", "Cone", "Fake_Pedestrian_Car", "Fake_Pedestrian", "Carton_S", "Carton_L", "Water_Barrier", "Fence", "Cone_S", "Anti_Collision_Bucket", "Triangle", "Bicycle", "Carton_M", "Fake_Car", "Traffic_Pole")
    obstacle = {"distance": {"God obstacle detection distance @center dist [1, 5]": {
        category: {"stable_det_dist": rounded(score * 25 - index * 0.2), "max_det_dist": rounded(score * 32 - index * 0.2)}
        for index, category in enumerate(obstacle_classes)
    }}}
    curb = {f"acc5_{region}": {"Avg inroad distance": rounded(errors * 0.3), "Inroad frames 0.4m+": 12, "Inroad frames 0.6m+": 5, "Inroad frames 1.0m+": 1}
            for region in ("0-20m", "20-40m", "40-60m", "60-90m")}
    det = bev_payload(score)
    return {
        "pnc_eval.json": pnc, "cog_eval.json": cog, "navi_eval.json": navi,
        "depth_eval.json": depth, "pvseg_rslt.json": pv,
        "bev_det.json": det, "bev_det_kof.json": bev_payload(min(0.97, score + 0.015)),
        "god_eval.json": god_payload(score), "god_obstacle.json": obstacle, "god_curb_inroad.json": curb,
        "god_raeb_obstacle_mis_detect.json": {"false_detection_frames": 4, "total_frames": 12000, "false_detection_grids_ratio": rounded(errors * 0.002)},
        "god_raeb_obstacle_distance.json": {
            "obstacle_pr": {"God obstacle @iou 0.1": {"BEV_dist@5": {"recall": rounded(score + 0.05)}, "BEV_dist@10": {"recall": rounded(score)}}},
            "distance": {"God obstacle detection distance @iou 0.1": {"ALL": {"stable_det_dist": rounded(score * 12), "max_det_dist": rounded(score * 15)}}},
        },
        "bev_ghost_probe.json": [{"false_positive_rate": rounded(errors * 0.03), "false_positive_frames": round(errors * 100), "total_frames": 12000}],
        "bev_cutin_latency.json": {"average_latency": rounded(errors * 0.5), "merge_left": {"latency": rounded(errors * 0.6), "gt_frame": 120, "pred_frame": 123}, "merge_right": {"latency": rounded(errors * 0.4), "gt_frame": 230, "pred_frame": 232}},
        "bev_cutin_latency_new.json": {tag: {"mean": rounded(errors * 0.5), "median": rounded(errors * 0.4), "max": rounded(errors * 0.9), "failed_num": 2} for tag in ("merge_left", "merge_right")},
    }


def plan_files():
    files = {}
    for experiment, offset in EXPERIMENTS.items():
        files[f"{experiment}/param.json"] = {
            "_synthetic_demo": True,
            "romaDetailUrl": f"https://example.com/experiments/{experiment}",
            "bucketName": "synthetic-demo", "outPath": experiment,
        }
        for iteration in CHECKPOINTS:
            payloads = family_payloads(quality(offset, iteration))
            if iteration == CHECKPOINTS[-1]:
                payloads["bev_move.json"] = bev_payload(quality(offset, iteration))
            for filename, payload in payloads.items():
                files[f"{experiment}/eval/iter_{iteration}/{filename}"] = payload
        start = dt.datetime(2026, 1, 1, 0, 0)
        lines = []
        for step in range(101):
            iteration = step * 100
            timestamp = start + dt.timedelta(seconds=iteration * (0.9 - offset))
            lines.append(f"{timestamp:%Y-%m-%d %H:%M:%S} Rank 0\ttrain:{iteration}/60000")
        files[f"{experiment}/training_logs/Rank-0.log"] = "\n".join(lines) + "\n"
    files["baseline-v1/STAR"] = "Synthetic baseline marker.\n"
    files["README.md"] = (
        "# 模拟演示数据\n\n所有指标均由 scripts/generate_demo.py 确定性生成，不能用来判断真实模型性能。\n\n"
        "包含 baseline-v1（STAR 基线）、candidate-v2、ablation，checkpoint 为 20k / 40k / 60k。\n"
        "支持 GOD、COG、BEV、PNC、PVSeg、Depth、Navi、BEV-new 默认评测及 GOD 各项模式。\n"
        "训练日志位于每个实验的 training_logs/，可用于训练速度对比；所有任务链接均指向 example.com。\n"
        "部分 BEV 专用区域文件未模拟；切换到这些文件时显示无数据是预期行为。\n"
    )
    files[MARKER] = {"generator": GENERATOR_ID, "synthetic": True}
    return files


def main():
    if ROOT is None:
        raise RuntimeError('测试必须显式指定临时评测目录')
    if ROOT.is_symlink():
        raise RuntimeError("拒绝写入符号链接 demo_data 目录")
    marker = ROOT / MARKER
    if ROOT.exists() and any(ROOT.iterdir()):
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError("demo_data 已有文件且没有模拟数据标识；拒绝覆盖")
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise RuntimeError("demo_data 模拟数据标识无效；拒绝覆盖") from error
        if previous.get("generator") != GENERATOR_ID or previous.get("synthetic") is not True:
            raise RuntimeError("demo_data 不属于当前模拟数据生成器；拒绝覆盖")
    files = plan_files()
    if len(files) > 150:
        raise RuntimeError("演示文件数量超出 150 文件上限")
    # Preflight every target before writing, so a symlink cannot redirect output.
    for relative in files:
        target = ROOT / relative
        for component in (target, *target.parents):
            if component == ROOT.parent:
                break
            if component.is_symlink():
                raise RuntimeError(f"拒绝通过符号链接写入：{component}")
    for relative, payload in files.items():
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        value = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        target.write_text(value, encoding="utf-8")
    print(f"已生成 {len(files)} 个模拟演示文件：{ROOT}")


