import AppKit
import Foundation

// Render the operator contracts as a portable, high-resolution PNG.
// Usage: swift docs/render_operator_io_table.swift docs/stage_b_operator_io.png

let output = CommandLine.arguments.count > 1
    ? URL(fileURLWithPath: CommandLine.arguments[1])
    : URL(fileURLWithPath: "docs/stage_b_operator_io.png")

let width = 2100
let height = 1510
let margin: CGFloat = 50
let tableTop: CGFloat = 215
let headerHeight: CGFloat = 68
let rowHeight: CGFloat = 112
let columns: [CGFloat] = [72, 310, 764, 854]

struct Row {
    let number: String
    let name: String
    let input: [String]
    let output: [String]
}

let rows: [Row] = [
    Row(number: "01", name: "输入转换", input: [
        "episode_index: int + PipelineConfig",
        "Stage A episode Parquet",
    ], output: [
        "QualityEpisode（dataclass）",
        "state/action: ndarray[T,D]；排除掩码: bool[T,D]",
    ]),
    Row(number: "02", name: "全局统计", input: [
        "episode_indices: list[int] + PipelineConfig",
    ], output: [
        "StatisticsBundle（dataclass）",
        "全局分位数 · Stage1统计 · 时间延迟；缓存 .npz",
    ]),
    Row(number: "03", name: "Stage1 突变检测", input: [
        "QualityEpisode + Stage1统计 + 配置",
    ], output: [
        "Stage1Result（dataclass）",
        "abnormal_frames: bool[T]；discard / reasons",
    ]),
    Row(number: "04", name: "Stage2 一致性检测", input: [
        "QualityEpisode + 全局时间延迟 + 配置",
    ], output: [
        "Stage2Result（dataclass）",
        "discard · DA分数 · 各维lag（无逐帧掩码）",
    ]),
    Row(number: "05", name: "Stage3 极值检测", input: [
        "QualityEpisode + GlobalStats + 配置",
    ], output: [
        "Stage3Result（dataclass）",
        "remove_frames: bool[T]；discard / reasons",
    ]),
    Row(number: "06", name: "前缀裁决", input: [
        "帧数 T + Stage1/2/3Result + 配置",
    ], output: [
        "PrefixDecision（dataclass）",
        "keep_mask: int8[T]；valid_end: int；trigger",
    ]),
    Row(number: "07", name: "Stage4 静止段处理", input: [
        "QualityEpisode + valid_end: int + 配置",
    ], output: [
        "StaticDecision（dataclass）",
        "remove_frames: bool[T]；Stage4Result",
    ]),
    Row(number: "08", name: "最终裁决", input: [
        "QualityEpisode + StatisticsBundle",
        "+ Stage1/2/3Result + PrefixDecision",
        "+ StaticDecision + 配置",
    ], output: [
        "EpisodeResult（dataclass）",
        "step_validity_mask: int8[T]；discard · audit",
    ]),
    Row(number: "09", name: "分片同步导出", input: [
        "Stage A目录 + 配置路径 + 输出目录",
        "+ 分片参数（内部运行 ①–⑧）",
    ], output: [
        "磁盘：LeRobot 分片目录",
        "Parquet · MP4 · JSON；run() 返回 int 状态码",
    ]),
    Row(number: "10", name: "数据集归并", input: [
        "shard_dirs: list[Path] + 配置路径",
        "+ 输出目录",
    ], output: [
        "磁盘：最终数据集 + merge_report.json",
        "run() 返回 dict（归并报告）",
    ]),
]

func color(_ hex: UInt32) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1)
}

let background = color(0xF3F6FA)
let navy = color(0x162B43)
let textColor = color(0x203047)
let secondary = color(0x53667B)
let lineColor = color(0xDDE5EC)
let teal = color(0x087B78)

func rect(_ x: CGFloat, _ top: CGFloat, _ w: CGFloat, _ h: CGFloat) -> NSRect {
    NSRect(x: x, y: CGFloat(height) - top - h, width: w, height: h)
}

func fill(_ x: CGFloat, _ top: CGFloat, _ w: CGFloat, _ h: CGFloat, _ c: NSColor) {
    c.setFill()
    NSBezierPath(rect: rect(x, top, w, h)).fill()
}

func draw(_ lines: [String], atX x: CGFloat, top: CGFloat,
          font: NSFont, ink: NSColor, lineHeight: CGFloat = 35) {
    let attributes: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: ink,
    ]
    for (index, line) in lines.enumerated() {
        let y = CGFloat(height) - top - CGFloat(index + 1) * lineHeight
        (line as NSString).draw(at: NSPoint(x: x, y: y), withAttributes: attributes)
    }
}

guard let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: width,
    pixelsHigh: height,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
), let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
    fatalError("Unable to allocate image")
}

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context
context.imageInterpolation = .high
fill(0, 0, CGFloat(width), CGFloat(height), background)

draw(["Stage B 十算子：输入与输出的数据结构"], atX: margin, top: 55,
     font: .systemFont(ofSize: 47, weight: .semibold), ink: navy, lineHeight: 58)
draw(["EgoStandard  ·  StridingAI / SLM_data_curation  ·  按当前封装代码的接口绘制"],
     atX: margin, top: 131,
     font: .systemFont(ofSize: 26, weight: .regular), ink: secondary, lineHeight: 38)

fill(margin, tableTop, 2000, headerHeight, navy)
let headings = ["序号", "算子", "输入", "输出"]
var x = margin
for (index, heading) in headings.enumerated() {
    draw([heading], atX: x + 19, top: tableTop + 13,
         font: .systemFont(ofSize: 29, weight: .semibold), ink: .white, lineHeight: 39)
    x += columns[index]
}

for (index, row) in rows.enumerated() {
    let top = tableTop + headerHeight + CGFloat(index) * rowHeight
    let disk = index >= 8
    fill(margin, top, 2000, rowHeight, disk ? color(0xE9F5F3) : (index.isMultiple(of: 2) ? .white : color(0xF8FAFC)))
    fill(margin, top + rowHeight - 1, 2000, 1, lineColor)
    if disk { fill(margin, top, 6, rowHeight, teal) }
    let starts: [CGFloat] = [margin, margin + columns[0], margin + columns[0] + columns[1],
                             margin + columns[0] + columns[1] + columns[2]]
    draw([row.number], atX: starts[0] + 18, top: top + 30,
         font: .monospacedDigitSystemFont(ofSize: 26, weight: .medium),
         ink: disk ? teal : secondary, lineHeight: 38)
    draw([row.name], atX: starts[1] + 18, top: top + 29,
         font: .systemFont(ofSize: 28, weight: .medium), ink: textColor, lineHeight: 39)
    let inputTop = top + (row.input.count == 3 ? 7 : row.input.count == 2 ? 22 : 38)
    let outputTop = top + (row.output.count == 3 ? 7 : row.output.count == 2 ? 22 : 38)
    draw(row.input, atX: starts[2] + 19, top: inputTop,
         font: .systemFont(ofSize: 25, weight: .regular), ink: textColor, lineHeight: 32)
    draw(row.output, atX: starts[3] + 19, top: outputTop,
         font: .systemFont(ofSize: 25, weight: .regular), ink: textColor, lineHeight: 32)
}

draw(["T = 帧数；D = 数据维度；bool[T] 为逐帧异常标记；int8[T] 中 1 = 保留、0 = 删除。"],
     atX: margin, top: 1433,
     font: .systemFont(ofSize: 24, weight: .regular), ink: secondary, lineHeight: 36)

context.flushGraphics()
NSGraphicsContext.restoreGraphicsState()

try FileManager.default.createDirectory(at: output.deletingLastPathComponent(),
                                        withIntermediateDirectories: true)
guard let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Unable to encode PNG")
}
try png.write(to: output)
print("Wrote \(output.path) (\(width)×\(height), \(png.count) bytes)")
