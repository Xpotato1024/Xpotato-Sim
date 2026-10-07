"""記録済みvector行をsample順のCSV/PNGへ変換する。serial/deviceは開かない。"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from collections.abc import Sequence

PALETTE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#17becf")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


@dataclass(frozen=True)
class Vector:
    timestamp_ms: int | None
    values: tuple[float, ...]


def parse_vector_line(line: str) -> Vector | None:
    parts = line.strip().split(",")
    if len(parts) < 9 or parts[0] != "vector":
        return None
    timestamp_text = parts[1].strip()
    timestamp = None
    if re.fullmatch(r"[+-]?[0-9]+", timestamp_text):
        candidate = int(timestamp_text)
        if -(2**63) <= candidate < 2**63:
            timestamp = candidate
    values = []
    for raw in parts[2:9]:
        value = raw.strip()
        # 旧Windows/.NET日本語cultureのNaN/∞とoverflow拒否を保持する。
        if value in {"∞", "-∞"}:
            parsed = math.inf if value == "∞" else -math.inf
        elif NUMBER.fullmatch(value):
            parsed = float(value)
            if math.isinf(parsed):
                parsed = math.nan
        else:
            parsed = math.nan
        values.append(parsed)
    return Vector(timestamp, tuple(values))


def change_extension(path: Path, extension: str) -> Path:
    # .NET Path.ChangeExtensionはleading dotもextensionとして扱う。
    name = path.name.rsplit(".", 1)[0] if "." in path.name else path.name
    return path.with_name(name + extension)


def default_output_path(source: str | None, now: datetime | None = None) -> Path:
    if source:
        return change_extension(Path(source), ".png")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return Path.cwd() / f"loadcell-vectors-{stamp}.png"


def read_clipboard() -> str:
    if os.name != "nt":
        raise RuntimeError("Clipboard input requires Windows; use --input-path or stdin")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); Get-Clipboard -Raw"],
        shell=False, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return result.stdout


def read_source(input_path: str | None, clipboard: bool) -> str:
    if clipboard:
        return read_clipboard()
    if input_path:
        # 旧ログのnon-vector commentが別encodingでもASCII vector行を保持する。
        return Path(input_path).read_text(encoding="utf-8-sig", errors="replace")
    return sys.stdin.read()


def csv_number(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "∞" if value > 0 else "-∞"
    return format(value, ".15g")


def write_csv(records: Sequence[Vector], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 旧PowerShell Export-CsvのUTF-8 BOM、quoted fields、全7chを維持する。
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
        writer.writerow(["sample_index", "timestamp_ms", *(f"ch{ch}" for ch in range(7))])
        for index, record in enumerate(records):
            writer.writerow([index, record.timestamp_ms, *(csv_number(v) for v in record.values)])


def write_chart(records: Sequence[Vector], path: Path, title: str, channels: Sequence[int]) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    path.parent.mkdir(parents=True, exist_ok=True)
    figure = Figure(figsize=(16, 9), dpi=100, facecolor="white")
    FigureCanvasAgg(figure)
    try:
        axis = figure.subplots()
        for channel in channels:
            if 0 <= channel <= 6:
                axis.plot(range(len(records)), [record.values[channel] for record in records],
                          label=f"ch{channel}", color=PALETTE[channel], linewidth=2)
        axis.set(title=title, xlabel="Sample index", ylabel="Value",
                 xlim=(0, max(1, len(records) - 1)))
        axis.tick_params(axis="x", labelrotation=45)
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, position: format(value, ".0f")))
        axis.grid(color="#dcdcdc")
        if axis.lines:
            axis.legend(loc="upper center", ncol=7)
        figure.savefig(path, format="png")
    finally:
        figure.clear()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-path", "-InputPath")
    parser.add_argument("--output-path", "-OutputPath")
    parser.add_argument("--csv-path", "-CsvPath")
    parser.add_argument("--clipboard", "-Clipboard", action="store_true")
    parser.add_argument("--title", "-Title", default="Loadcell vectors")
    parser.add_argument("--channels", "-Channels", nargs="+", default=["0,1,2,3,4,5,6"])
    parser.add_argument("-Help", action="help")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        channels = [int(part) for item in args.channels for part in item.split(",")]
    except ValueError:
        parser.error("channels must be integers separated by spaces or commas")
    try:
        records = [record for line in read_source(args.input_path, args.clipboard).splitlines()
                   if (record := parse_vector_line(line)) is not None]
        if not records:
            raise ValueError("No vector lines were found.")
        output = Path(args.output_path) if args.output_path else default_output_path(args.input_path)
        csv_path = Path(args.csv_path) if args.csv_path else change_extension(output, ".csv")
        write_csv(records, csv_path)
        write_chart(records, output, args.title, channels)
        print(f"Parsed vectors : {len(records)}")
        print(f"PNG output     : {output}")
        print(f"CSV output     : {csv_path}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
