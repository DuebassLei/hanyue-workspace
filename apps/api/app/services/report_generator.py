"""报告生成引擎 — PDF 与 Excel 导出 (Phase 3)"""

from typing import Any
from datetime import datetime
import io
import json


class ReportGenerator:
    """报告生成引擎 — 支持 PDF 与 Excel 格式"""

    def __init__(self):
        """初始化报告生成器"""
        self.report_title = "文章原创度检测报告"
        self.report_version = "1.0"

    def generate_report_data(
        self,
        analysis: dict[str, Any],
        content_info: dict[str, Any],
        include_details: bool = True,
    ) -> dict[str, Any]:
        """
        生成报告数据（JSON 格式）
        
        Args:
            analysis: 分析结果
            content_info: 文章信息（标题、作者等）
            include_details: 是否包含详细数据
            
        Returns:
            报告数据字典
        """
        report = {
            "report_meta": {
                "title": self.report_title,
                "version": self.report_version,
                "generated_at": datetime.now().isoformat(),
                "content_id": content_info.get("id", "unknown"),
                "content_title": content_info.get("title", "未命名文章"),
                "content_author": content_info.get("author", "未知"),
            },
            "summary": {
                "total_score": analysis.get("totalScore", 0),
                "level": analysis.get("level", "poor"),
                "level_label": self._get_level_label(analysis.get("level", "poor")),
                "analysis_time_ms": analysis.get("processingTimeMs", 0),
            },
            "scores": analysis.get("scoreboard", {}),
            "details": analysis.get("details", {}) if include_details else {},
            "recommendations": analysis.get("recommendations", []),
            "generated_timestamp": datetime.now().timestamp(),
        }

        return report

    def _get_level_label(self, level: str) -> str:
        """获取等级标签"""
        labels = {
            "excellent": "优秀 (90-100)",
            "good": "良好 (75-89)",
            "fair": "中等 (60-74)",
            "poor": "低 (0-59)",
        }
        return labels.get(level, "未知")

    def generate_pdf(
        self,
        report_data: dict[str, Any],
    ) -> bytes:
        """
        生成 PDF 报告
        
        Args:
            report_data: 报告数据
            
        Returns:
            PDF 文件内容（字节）
        """
        try:
            from reportlab.lib.pagesizes import letter, A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch, cm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
            from reportlab.lib import colors
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from io import BytesIO

            # 创建 PDF 缓冲区
            pdf_buffer = BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=1*cm, bottomMargin=1*cm)

            # 样式定义
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                "CustomTitle",
                parent=styles["Heading1"],
                fontSize=24,
                textColor=colors.HexColor("#1a237e"),
                spaceAfter=30,
                alignment=1,  # 居中
            )

            # 构建文档内容
            story = []

            # 标题
            story.append(Paragraph("文章原创度检测报告", title_style))
            story.append(Spacer(1, 0.3*cm))

            # 基本信息表格
            meta = report_data["report_meta"]
            info_data = [
                ["信息项", "内容"],
                ["文章标题", meta.get("content_title", "N/A")],
                ["作者", meta.get("content_author", "N/A")],
                ["生成时间", meta.get("generated_at", "N/A")[:19]],
            ]
            info_table = Table(info_data)
            info_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a237e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]))
            story.append(info_table)
            story.append(Spacer(1, 0.5*cm))

            # 总分卡片
            summary = report_data["summary"]
            level_colors = {
                "excellent": colors.green,
                "good": colors.blue,
                "fair": colors.orange,
                "poor": colors.red,
            }
            score_color = level_colors.get(summary["level"], colors.gray)

            score_data = [
                ["综合评分", f"{summary['total_score']}/100"],
                ["等级", summary["level_label"]],
            ]
            score_table = Table(score_data)
            score_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), score_color),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 14),
                ("PADDING", (0, 0), (-1, -1), 20),
                ("GRID", (0, 0), (-1, -1), 1, colors.whitesmoke),
            ]))
            story.append(score_table)
            story.append(Spacer(1, 0.5*cm))

            # 五维度评分
            scores = report_data["scores"]
            story.append(Paragraph("五维度评分", styles["Heading2"]))
            scores_data = [
                ["维度", "评分", "占比"],
                ["相似度", f"{scores.get('similarity', 0)}", "40%"],
                ["观点独特性", f"{scores.get('originality', 0)}", "25%"],
                ["结构创新度", f"{scores.get('structure', 0)}", "15%"],
                ["数据真实度", f"{scores.get('dataAccuracy', 0)}", "12%"],
                ["语言风格", f"{scores.get('styleUniqueness', 0)}", "8%"],
            ]
            scores_table = Table(scores_data)
            scores_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a237e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
            ]))
            story.append(scores_table)
            story.append(Spacer(1, 0.5*cm))

            # 改进建议
            story.append(Paragraph("改进建议", styles["Heading2"]))
            for idx, rec in enumerate(report_data.get("recommendations", []), 1):
                story.append(Paragraph(f"{idx}. {rec}", styles["Normal"]))
            story.append(Spacer(1, 0.3*cm))

            # 页脚
            story.append(Spacer(1, 1*cm))
            story.append(Paragraph(
                f"报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                styles["Normal"],
            ))

            # 生成 PDF
            doc.build(story)
            return pdf_buffer.getvalue()

        except ImportError:
            print("reportlab 未安装，返回 JSON 报告")
            return json.dumps(report_data).encode("utf-8")

    def generate_excel(self, report_data: dict[str, Any]) -> bytes:
        """
        生成 Excel 报告
        
        Args:
            report_data: 报告数据
            
        Returns:
            Excel 文件内容（字节）
        """
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from io import BytesIO

            wb = Workbook()
            ws = wb.active
            ws.title = "检测报告"

            # 标题行
            ws["A1"] = "文章原创度检测报告"
            ws["A1"].font = Font(size=16, bold=True, color="FFFFFF")
            ws["A1"].fill = PatternFill(start_color="1a237e", end_color="1a237e", fill_type="solid")
            ws.merge_cells("A1:D1")

            # 基本信息
            row = 3
            meta = report_data["report_meta"]

            ws[f"A{row}"] = "文章标题"
            ws[f"B{row}"] = meta.get("content_title", "N/A")
            row += 1

            ws[f"A{row}"] = "作者"
            ws[f"B{row}"] = meta.get("content_author", "N/A")
            row += 1

            ws[f"A{row}"] = "生成时间"
            ws[f"B{row}"] = meta.get("generated_at", "N/A")[:19]
            row += 2

            # 总分信息
            summary = report_data["summary"]
            ws[f"A{row}"] = "综合评分"
            ws[f"B{row}"] = summary["total_score"]
            ws[f"C{row}"] = "等级"
            ws[f"D{row}"] = summary["level_label"]
            row += 2

            # 五维度评分表
            ws[f"A{row}"] = "维度"
            ws[f"B{row}"] = "评分"
            ws[f"C{row}"] = "占比"

            # 设置表头样式
            for col in ["A", "B", "C"]:
                ws[f"{col}{row}"].font = Font(bold=True, color="FFFFFF")
                ws[f"{col}{row}"].fill = PatternFill(start_color="1a237e", end_color="1a237e", fill_type="solid")

            row += 1
            scores = report_data["scores"]
            score_items = [
                ("相似度", scores.get("similarity", 0), "40%"),
                ("观点独特性", scores.get("originality", 0), "25%"),
                ("结构创新度", scores.get("structure", 0), "15%"),
                ("数据真实度", scores.get("dataAccuracy", 0), "12%"),
                ("语言风格", scores.get("styleUniqueness", 0), "8%"),
            ]

            for dimension, score, weight in score_items:
                ws[f"A{row}"] = dimension
                ws[f"B{row}"] = score
                ws[f"C{row}"] = weight
                row += 1

            row += 1

            # 改进建议
            ws[f"A{row}"] = "改进建议"
            ws[f"A{row}"].font = Font(bold=True, size=12)
            row += 1

            for rec in report_data.get("recommendations", []):
                ws[f"A{row}"] = rec
                ws[f"A{row}"].alignment = Alignment(wrap_text=True)
                row += 1

            # 调整列宽
            ws.column_dimensions["A"].width = 20
            ws.column_dimensions["B"].width = 15
            ws.column_dimensions["C"].width = 15
            ws.column_dimensions["D"].width = 20

            # 生成 Excel
            excel_buffer = BytesIO()
            wb.save(excel_buffer)
            excel_buffer.seek(0)
            return excel_buffer.getvalue()

        except ImportError:
            print("openpyxl 未安装，返回 JSON 报告")
            return json.dumps(report_data).encode("utf-8")

    def generate_markdown(self, report_data: dict[str, Any]) -> str:
        """
        生成 Markdown 格式报告
        
        Args:
            report_data: 报告数据
            
        Returns:
            Markdown 字符串
        """
        md = []

        # 标题
        md.append("# 文章原创度检测报告")
        md.append("")

        # 基本信息
        meta = report_data["report_meta"]
        md.append("## 基本信息")
        md.append(f"- **文章标题**: {meta.get('content_title', 'N/A')}")
        md.append(f"- **作者**: {meta.get('content_author', 'N/A')}")
        md.append(f"- **生成时间**: {meta.get('generated_at', 'N/A')[:19]}")
        md.append("")

        # 总分
        summary = report_data["summary"]
        md.append("## 检测结果")
        md.append(f"- **综合评分**: {summary['total_score']}/100")
        md.append(f"- **等级**: {summary['level_label']}")
        md.append("")

        # 五维度评分
        md.append("## 五维度评分")
        md.append("| 维度 | 评分 | 占比 |")
        md.append("|------|------|------|")

        scores = report_data["scores"]
        score_items = [
            ("相似度", scores.get("similarity", 0), "40%"),
            ("观点独特性", scores.get("originality", 0), "25%"),
            ("结构创新度", scores.get("structure", 0), "15%"),
            ("数据真实度", scores.get("dataAccuracy", 0), "12%"),
            ("语言风格", scores.get("styleUniqueness", 0), "8%"),
        ]

        for dimension, score, weight in score_items:
            md.append(f"| {dimension} | {score} | {weight} |")

        md.append("")

        # 改进建议
        md.append("## 改进建议")
        for idx, rec in enumerate(report_data.get("recommendations", []), 1):
            md.append(f"{idx}. {rec}")

        md.append("")
        md.append(f"---\n_报告生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")

        return "\n".join(md)
