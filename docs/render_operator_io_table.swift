import AppKit
import Foundation

// Render the 16-class v2 Stage B dataflow on macOS.
// Usage: swift docs/render_operator_io_table.swift docs/stage_b_operator_io.png

let output = CommandLine.arguments.count > 1
    ? URL(fileURLWithPath: CommandLine.arguments[1])
    : URL(fileURLWithPath: "docs/stage_b_operator_io.png")

let width = 3000
let height = 2180
let margin: CGFloat = 95
let cardWidth: CGFloat = 665
let cardHeight: CGFloat = 170
let cardX: [CGFloat] = [95, 810, 1525, 2240]

func color(_ hex: UInt32) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1)
}

let background = color(0xF4F7FB)
let navy = color(0x162B43)
let ink = color(0x24374E)
let secondary = color(0x5B6E82)
let line = color(0xC7D4E1)
let blue = color(0x3165A4)
let teal = color(0x087F77)
let amber = color(0xA7641B)

func rect(_ x: CGFloat, _ top: CGFloat, _ w: CGFloat, _ h: CGFloat) -> NSRect {
    NSRect(x: x, y: CGFloat(height) - top - h, width: w, height: h)
}

func panel(_ x: CGFloat, _ top: CGFloat, _ w: CGFloat, _ h: CGFloat,
           fill: NSColor, border: NSColor = line, radius: CGFloat = 24) {
    let path = NSBezierPath(roundedRect: rect(x, top, w, h), xRadius: radius, yRadius: radius)
    fill.setFill()
    path.fill()
    border.setStroke()
    path.lineWidth = 2
    path.stroke()
}

func text(_ value: String, x: CGFloat, top: CGFloat, width: CGFloat, h: CGFloat,
          size: CGFloat, weight: NSFont.Weight = .regular, tone: NSColor = ink) {
    let style = NSMutableParagraphStyle()
    style.lineBreakMode = .byWordWrapping
    style.alignment = .left
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: size, weight: weight),
        .foregroundColor: tone,
        .paragraphStyle: style,
    ]
    (value as NSString).draw(in: rect(x, top, width, h), withAttributes: attrs)
}

func card(_ index: Int, _ top: CGFloat, title: String, detail: String,
          result: String, accent: NSColor, fill: NSColor = .white) {
    let x = cardX[index]
    panel(x, top, cardWidth, cardHeight, fill: fill, border: line, radius: 18)
    panel(x + 18, top + 23, 7, cardHeight - 46, fill: accent, border: accent, radius: 3)
    text(title, x: x + 44, top: top + 22, width: cardWidth - 65, h: 43,
         size: 31, weight: .semibold, tone: navy)
    text(detail, x: x + 44, top: top + 71, width: cardWidth - 65, h: 36,
         size: 25, tone: secondary)
    text(result, x: x + 44, top: top + 116, width: cardWidth - 65, h: 40,
         size: 27, weight: .medium, tone: accent)
}

func horizontal(_ x1: CGFloat, _ x2: CGFloat, top: CGFloat,
                tone: NSColor = secondary, arrowhead: Bool = true) {
    let y = CGFloat(height) - top
    let path = NSBezierPath()
    path.move(to: NSPoint(x: x1, y: y))
    path.line(to: NSPoint(x: x2, y: y))
    tone.setStroke()
    path.lineWidth = 5
    path.stroke()
    if arrowhead {
        let tip = NSBezierPath()
        tip.move(to: NSPoint(x: x2, y: y))
        tip.line(to: NSPoint(x: x2 - 17, y: y + 10))
        tip.line(to: NSPoint(x: x2 - 17, y: y - 10))
        tip.close()
        tone.setFill()
        tip.fill()
    }
}

func vertical(_ x: CGFloat, _ top1: CGFloat, _ top2: CGFloat,
              tone: NSColor = secondary, arrowhead: Bool = true) {
    let path = NSBezierPath()
    path.move(to: NSPoint(x: x, y: CGFloat(height) - top1))
    path.line(to: NSPoint(x: x, y: CGFloat(height) - top2))
    tone.setStroke()
    path.lineWidth = 5
    path.stroke()
    if arrowhead {
        let tipY = CGFloat(height) - top2
        let tip = NSBezierPath()
        tip.move(to: NSPoint(x: x, y: tipY))
        tip.line(to: NSPoint(x: x - 10, y: tipY + 17))
        tip.line(to: NSPoint(x: x + 10, y: tipY + 17))
        tip.close()
        tone.setFill()
        tip.fill()
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
background.setFill()
NSBezierPath(rect: rect(0, 0, CGFloat(width), CGFloat(height))).fill()

text("Stage B v2｜16 个算子的输入、输出与数据流", x: margin, top: 55,
     width: 2800, h: 75, size: 54, weight: .bold, tone: navy)
text("StridingAI / SLM_data_curation · 12 个 Stage B 数据集包 · 输入为 Stage A pseudo_v3",
     x: margin, top: 137, width: 2800, h: 45, size: 30, tone: secondary)

// Dataset-level control and shared statistics.
panel(70, 230, 2860, 315, fill: color(0xEAF1FA), border: color(0xC9D9EC))
text("01  数据集级准备", x: 105, top: 250, width: 1200, h: 45,
     size: 31, weight: .bold, tone: blue)
card(0, 315, title: "ConfigOperator", detail: "仓库 + 数据集选择 / YAML",
     result: "→ StageBDatasetSpec", accent: blue)
card(1, 315, title: "GateOperator", detail: "Spec + review_checklist.yaml",
     result: "→ GateDecision", accent: blue)
card(2, 315, title: "DiscoverOperator", detail: "Spec + pseudo_v3 根目录",
     result: "→ list[EpisodeRef]", accent: blue)
card(3, 315, title: "StatisticsOperator", detail: "EpisodeRef[] + 质量配置",
     result: "→ StatisticsBundle", accent: blue)
for i in 0..<3 {
    horizontal(cardX[i] + cardWidth + 10, cardX[i + 1] - 10, top: 400, tone: blue)
}
vertical(cardX[3] + cardWidth / 2, 485, 580, tone: blue)

// QualityOperator composes the seven cards inside this group. Stage1–3 each
// consume the same QualityEpisode and statistics; their outputs meet at Prefix.
panel(70, 590, 2860, 955, fill: color(0xEBF8F5), border: color(0xBBDCD5))
text("02  Episode 级质量筛选", x: 105, top: 610, width: 700, h: 48,
     size: 32, weight: .bold, tone: teal)
panel(875, 609, 850, 54, fill: color(0xD7F0E9), border: color(0xBBDCD5), radius: 14)
text("QualityOperator：组合以下 7 个子算子", x: 902, top: 619,
     width: 795, h: 35, size: 27, weight: .semibold, tone: teal)
text("QualityEpisode + StatisticsBundle → EpisodeResult", x: 1800,
     top: 618, width: 1050, h: 36, size: 25, tone: teal)

card(0, 770, title: "InputOperator", detail: "EpisodeRef + 数据集 adapter",
     result: "→ QualityEpisode", accent: teal)
card(1, 770, title: "Stage1Operator", detail: "突变检测 + 全局阈值",
     result: "→ Stage1Result", accent: teal)
card(2, 770, title: "Stage2Operator", detail: "趋势 / DA 一致性 + lag",
     result: "→ Stage2Result", accent: teal)
card(3, 770, title: "Stage3Operator", detail: "极值检测 + 全局分位数",
     result: "→ Stage3Result", accent: teal)

// Shared input bus above Stage1–3.
vertical(cardX[0] + cardWidth / 2, 770, 715, tone: teal, arrowhead: false)
horizontal(cardX[0] + cardWidth / 2, cardX[3] + cardWidth / 2, top: 715,
           tone: teal, arrowhead: false)
for i in 1...3 {
    vertical(cardX[i] + cardWidth / 2, 715, 758, tone: teal)
}

// Three stage decisions join at the prefix decision.
for i in 1...3 {
    vertical(cardX[i] + cardWidth / 2, 940, 1050, tone: teal, arrowhead: false)
}
horizontal(cardX[1] + cardWidth / 2, cardX[3] + cardWidth / 2,
           top: 1050, tone: teal, arrowhead: false)
vertical(cardX[1] + cardWidth / 2, 1050, 1178, tone: teal)
text("Stage1 / Stage2 / Stage3 结果汇聚", x: 1060, top: 1080,
     width: 1000, h: 35, size: 25, tone: secondary)

card(1, 1190, title: "PrefixOperator", detail: "Stage1/2/3 的异常结果",
     result: "→ PrefixDecision", accent: teal)
card(2, 1190, title: "Stage4Operator", detail: "前缀内静止片段处理",
     result: "→ StaticDecision", accent: teal)
card(3, 1190, title: "FinalDecisionOperator", detail: "阶段结果 + 掩码 + 审计",
     result: "→ EpisodeResult", accent: teal)
horizontal(cardX[1] + cardWidth + 10, cardX[2] - 10, top: 1275, tone: teal)
horizontal(cardX[2] + cardWidth + 10, cardX[3] - 10, top: 1275, tone: teal)
text("Stage5 仅在上游质量 YAML 显式启用时执行；分辨率预处理也由上游配置控制。",
     x: 105, top: 1435, width: 2760, h: 45, size: 26, tone: secondary)

// Output paths. Report is a side branch, while aligned data and masks feed
// export; the export shard is then merged into the final dataset.
panel(70, 1620, 2860, 400, fill: color(0xFFF6E7), border: color(0xECD7B5))
text("03  报告与导出", x: 105, top: 1642, width: 650, h: 48,
     size: 32, weight: .bold, tone: amber)
text("EpisodeResult → 报告；EpisodeRef → AlignedEpisode；两者进入导出，再归并分片",
     x: 720, top: 1647, width: 2100, h: 40, size: 27, tone: secondary)
card(0, 1735, title: "AlignedEpisodeOperator", detail: "EpisodeRef + aligned adapter",
     result: "→ AlignedEpisode", accent: amber)
card(1, 1735, title: "ReportOperator", detail: "EpisodeResult[] + 统计审计",
     result: "→ 3 份质量 JSON/JSONL", accent: amber)
card(2, 1735, title: "ExportOperator", detail: "对齐 Episode + 保留掩码",
     result: "→ LeRobot 分片", accent: amber)
card(3, 1735, title: "AggregateOperator", detail: "分片目录 + merge_shards.py",
     result: "→ 最终集 + relative stats", accent: amber)
horizontal(cardX[2] + cardWidth + 10, cardX[3] - 10, top: 1820, tone: amber)

text("数据结构为 Python 对象；Parquet / MP4 / JSON 是落盘结果。输出布局由各数据集 YAML 决定：pseudo_v3 或 aggregated_v3。",
     x: margin, top: 2075, width: 2800, h: 46, size: 25, tone: secondary)

context.flushGraphics()
NSGraphicsContext.restoreGraphicsState()

try FileManager.default.createDirectory(at: output.deletingLastPathComponent(),
                                        withIntermediateDirectories: true)
guard let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Unable to encode PNG")
}
try png.write(to: output)
print("Wrote \(output.path) (\(width)×\(height), \(png.count) bytes)")
