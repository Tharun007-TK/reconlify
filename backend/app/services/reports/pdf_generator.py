import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

class PDFReportGenerator:
    """Generates an executive PDF report for a reconciliation run."""

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.styles = getSampleStyleSheet()
        # Custom styles
        self.styles.add(ParagraphStyle(name='TitleStyle', fontSize=18, spaceAfter=14, textColor=colors.HexColor("#1F4E78")))
        self.styles.add(ParagraphStyle(name='Heading2Style', fontSize=14, spaceAfter=10, spaceBefore=15, textColor=colors.HexColor("#2C3E50")))
        self.styles.add(ParagraphStyle(name='NormalStyle', fontSize=10, spaceAfter=6))
        self.styles.add(ParagraphStyle(name='AlertStyle', fontSize=10, spaceAfter=6, textColor=colors.red))

    def generate(self) -> bytes:
        buf = io.BytesIO()
        # Use landscape for wider tables
        doc = SimpleDocTemplate(buf, pagesize=landscape(letter), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
        
        elements = []
        
        # 1. Executive Summary
        self._build_executive_summary(elements)
        elements.append(PageBreak())
        
        # 2. Mismatch Analysis (Top exceptions)
        self._build_mismatch_analysis(elements)
        elements.append(PageBreak())
        
        # 3. Vendor Analysis
        self._build_vendor_analysis(elements)
        
        doc.build(elements)
        return buf.getvalue()

    def _build_executive_summary(self, elements: list) -> None:
        run = self.data.get("run", {})
        
        elements.append(Paragraph("GST Reconciliation Executive Summary", self.styles['TitleStyle']))
        elements.append(Spacer(1, 10))
        
        status = run.get('status', 'N/A').upper()
        elements.append(Paragraph(f"<b>Run Status:</b> {status}", self.styles['NormalStyle']))
        elements.append(Paragraph(f"<b>Started:</b> {run.get('started_at', 'N/A')}", self.styles['NormalStyle']))
        elements.append(Spacer(1, 10))
        
        # Key Metrics Table
        metrics_data = [
            ["Metric", "Value"],
            ["Total Matched", str(run.get('matched_count', 0))],
            ["Total Unmatched (PR)", str(run.get('unmatched_pr_count', 0))],
            ["Total Unmatched (2B)", str(run.get('unmatched_2b_count', 0))],
            ["Total Duplicates", str(run.get('duplicate_count', 0))],
            ["Match Rate", f"{run.get('match_rate', 0.0) * 100:.2f}%"],
            ["Total ITC Claimed", f"₹{run.get('total_itc_claimed', 0.0):,.2f}"],
            ["ITC Matched (Safe)", f"₹{run.get('itc_matched', 0.0):,.2f}"],
            ["ITC At Risk", f"₹{run.get('itc_at_risk', 0.0):,.2f}"]
        ]
        
        t = Table(metrics_data, colWidths=[2*inch, 2*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F4E78")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#F2F2F2")),
            ('GRID', (0,0), (-1,-1), 1, colors.white)
        ]))
        elements.append(t)
        
        elements.append(Spacer(1, 20))
        
        # Recommendations
        elements.append(Paragraph("High-Level Recommendations", self.styles['Heading2Style']))
        if run.get('itc_at_risk', 0) > 100000:
            elements.append(Paragraph("• <b>CRITICAL:</b> Over ₹1,00,000 in ITC is at risk. Immediate follow-up with top non-compliant vendors is required.", self.styles['AlertStyle']))
        if run.get('match_rate', 0) < 0.8:
            elements.append(Paragraph("• <b>WARNING:</b> Match rate is below 80%. Review potential systematic invoice numbering mismatches.", self.styles['AlertStyle']))
        elements.append(Paragraph("• Review 'Unmatched (PR)' entries as these represent purchases not yet filed by suppliers.", self.styles['NormalStyle']))

    def _build_mismatch_analysis(self, elements: list) -> None:
        elements.append(Paragraph("Mismatch Analysis (High & Critical)", self.styles['TitleStyle']))
        
        unmatched = self.data.get("unmatched", [])
        critical_high = [u for u in unmatched if u.get("severity") in ("Critical", "High")]
        
        if not critical_high:
            elements.append(Paragraph("No critical or high severity mismatches found.", self.styles['NormalStyle']))
            return
            
        # Top 50 to prevent PDF blowout
        critical_high = critical_high[:50]
            
        data = [["Category", "Severity", "GSTIN", "Invoice", "Taxable Value", "Reason"]]
        for u in critical_high:
            data.append([
                u.get("category", "")[:20],
                u.get("severity", ""),
                u.get("gstin_supplier", ""),
                u.get("invoice_number", ""),
                f"₹{u.get('taxable_value', 0):,.2f}",
                u.get("reason", "")[:40]
            ])
            
        t = Table(data, colWidths=[1.5*inch, 1*inch, 1.5*inch, 1.2*inch, 1.2*inch, 3*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F4E78")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey)
        ]))
        elements.append(t)

    def _build_vendor_analysis(self, elements: list) -> None:
        elements.append(Paragraph("Vendor Analysis (Top Risks)", self.styles['TitleStyle']))
        
        vendors = self.data.get("vendors", [])
        if not vendors:
            elements.append(Paragraph("No vendor data available.", self.styles['NormalStyle']))
            return
            
        # Top 50 vendors by risk
        vendors = vendors[:50]
        
        data = [["Risk", "Vendor Name", "GSTIN", "Mismatch Rate", "ITC Claimed", "ITC At Risk"]]
        for v in vendors:
            data.append([
                v.get("risk_level", "").upper(),
                v.get("name", "")[:25],
                v.get("gstin", ""),
                f"{v.get('mismatch_rate', 0)*100:.1f}%",
                f"₹{v.get('itc_claimed', 0):,.2f}",
                f"₹{v.get('itc_at_risk', 0):,.2f}",
            ])
            
        t = Table(data, colWidths=[1*inch, 2*inch, 1.5*inch, 1.2*inch, 1.5*inch, 1.5*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F4E78")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey)
        ]))
        elements.append(t)
