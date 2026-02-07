#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LG SPORT - PDF Generator (Professional)

Générateur de rapports PDF professionnels.
Version: 3.0
Date: 2026-02-01
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class PDFGenerator:
    """Générateur de rapports PDF pour le scouting football."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.pdf_settings: dict[str, Any] = config.get("pdf_settings", {})

        base_dir = Path(__file__).resolve().parent
        output_dir = self.pdf_settings.get("output_dir", "output_pdfs")
        self.output_dir = str((base_dir / output_dir).resolve())
        os.makedirs(self.output_dir, exist_ok=True)

        self.system_version = str(config.get("metadata", {}).get("version", "3.0"))

        self.colors = {
            "primary": colors.HexColor(self.pdf_settings.get("color_primary", "#1E40AF")),
            "secondary": colors.HexColor(self.pdf_settings.get("color_secondary", "#10B981")),
            "warning": colors.HexColor(self.pdf_settings.get("color_warning", "#F59E0B")),
            "error": colors.HexColor(self.pdf_settings.get("color_error", "#EF4444")),
            "text": colors.HexColor("#212121"),
            "text_light": colors.HexColor("#757575"),
            "background": colors.HexColor("#F5F5F5"),
        }

        logger.info(f"📄 PDFGenerator V{self.system_version} initialisé")

    def generate_report(self, entity_data: dict[str, Any], entity_type: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"lg_sport_v{self.system_version}_{entity_type}_{timestamp}.pdf"
        filepath = os.path.join(self.output_dir, filename)

        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=25 * mm,
            bottomMargin=25 * mm,
        )

        story: list[Any] = []

        if self.pdf_settings.get("include_cover_page", True):
            story.extend(self._build_cover_page(entity_type, entity_data))
            story.append(PageBreak())

        styles = self._get_styles()

        if self.pdf_settings.get("include_summary", True) and entity_data.get("summary"):
            story.append(Paragraph("RÉSUMÉ", styles["Heading1"]))
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph(str(entity_data.get("summary")), styles["Normal"]))
            story.append(Spacer(1, 6 * mm))

        if isinstance(entity_data.get("reliability"), dict):
            rel = entity_data["reliability"]
            story.append(Paragraph("FIABILITÉ DES DONNÉES", styles["Heading1"]))
            story.append(Spacer(1, 3 * mm))
            rel_rows = [
                ["Confiance moyenne", str(rel.get("avg_confidence", "N/A"))],
                ["Champs (total)", str(rel.get("fields_total", "N/A"))],
                ["Champs majorité", str(rel.get("fields_majority", "N/A"))],
                ["Champs incertains", str(rel.get("fields_uncertain", "N/A"))],
                ["Champs manquants", str(rel.get("fields_missing", "N/A"))],
            ]
            story.append(self._create_info_table(rel_rows))
            story.append(Spacer(1, 6 * mm))

        if self.pdf_settings.get("include_charts", True) and entity_data.get("charts"):
            story.extend(self._build_charts(entity_data))
            story.append(Spacer(1, 6 * mm))

        if entity_type == "player":
            story.extend(self._build_player_report(entity_data))
        elif entity_type == "team":
            story.extend(self._build_team_report(entity_data))
        elif entity_type == "match":
            story.extend(self._build_match_report(entity_data))
        else:
            story.extend(self._build_generic_report(entity_type, entity_data))

        story.append(PageBreak())
        story.extend(self._build_metadata_page(entity_data))

        doc.build(story)
        logger.info(f"✅ PDF généré: {filepath}")
        return filepath

    def _build_cover_page(self, entity_type: str, data: dict[str, Any]) -> list[Any]:
        story: list[Any] = []
        styles = self._get_styles()

        story.append(Spacer(1, 50 * mm))

        story.append(Paragraph("RAPPORT D'ANALYSE", styles["Title"]))
        story.append(Spacer(1, 10 * mm))

        entity_name = self._get_entity_name(data, entity_type)
        story.append(Paragraph(f"<b>{entity_name}</b>", styles["Heading1"]))
        story.append(Spacer(1, 5 * mm))

        type_labels = {"player": "Joueur", "team": "Équipe", "match": "Match"}
        type_text = type_labels.get(entity_type, entity_type.capitalize())
        story.append(Paragraph(type_text, styles["Heading2"]))

        story.append(Spacer(1, 20 * mm))
        story.append(Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", styles["Normal"]))

        logo_path = self.pdf_settings.get("logo_path", "")
        if logo_path and os.path.exists(logo_path):
            from reportlab.platypus import Image

            story.append(Spacer(1, 10 * mm))
            story.append(Image(logo_path, width=50 * mm, height=50 * mm))

        return story

    def _build_player_report(self, data: dict[str, Any]) -> list[Any]:
        story: list[Any] = []
        styles = self._get_styles()

        story.append(Paragraph("INFORMATIONS JOUEUR", styles["Heading1"]))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Informations personnelles", styles["Heading2"]))
        personal_data = [
            ["Nom complet", self._get_value(data, "full_name")],
            ["Nom usuel", self._get_value(data, "name")],
            ["Date de naissance", self._get_value(data, "date_of_birth")],
            ["Nationalité", self._get_value(data, "nationality")],
            ["Taille", self._get_value(data, "height")],
            ["Poids", self._get_value(data, "weight")],
        ]
        story.append(self._create_info_table(personal_data))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Informations sportives", styles["Heading2"]))
        sport_data = [
            ["Position", self._get_value(data, "position")],
            ["Équipe actuelle", self._get_value(data, "current_team")],
            ["Numéro de maillot", self._get_value(data, "shirt_number")],
        ]
        story.append(self._create_info_table(sport_data))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Traçabilité des données", styles["Heading2"]))
        sources, source_count, consensus = self._extract_fusion_meta(data)
        meta_data = [
            ["Sources consultées", ", ".join(sources) if sources else "N/A"],
            ["Nombre de sources", str(source_count) if source_count is not None else "N/A"],
            ["Consensus", consensus or "N/A"],
            ["Qualité des données", self._assess_data_quality(data)],
        ]
        story.append(self._create_info_table(meta_data))

        return story

    def _build_team_report(self, data: dict[str, Any]) -> list[Any]:
        story: list[Any] = []
        styles = self._get_styles()

        story.append(Paragraph("INFORMATIONS ÉQUIPE", styles["Heading1"]))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Informations générales", styles["Heading2"]))
        general_data = [
            ["Nom complet", self._get_value(data, "name")],
            ["Nom court", self._get_value(data, "short_name")],
            ["Fondation", self._get_value(data, "founded")],
            ["Pays", self._get_value(data, "country")],
            ["Ville", self._get_value(data, "city")],
            ["Ligue", self._get_value(data, "league")],
        ]
        story.append(self._create_info_table(general_data))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Installations", styles["Heading2"]))
        facilities_data = [
            ["Stade", self._get_value(data, "stadium")],
            ["Capacité", self._get_value(data, "capacity")],
            ["Entraîneur", self._get_value(data, "coach")],
        ]
        story.append(self._create_info_table(facilities_data))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Traçabilité des données", styles["Heading2"]))
        sources, source_count, consensus = self._extract_fusion_meta(data)
        meta_data = [
            ["Sources consultées", ", ".join(sources) if sources else "N/A"],
            ["Nombre de sources", str(source_count) if source_count is not None else "N/A"],
            ["Consensus", consensus or "N/A"],
            ["Qualité des données", self._assess_data_quality(data)],
        ]
        story.append(self._create_info_table(meta_data))

        return story

    def _build_match_report(self, data: dict[str, Any]) -> list[Any]:
        story: list[Any] = []
        styles = self._get_styles()

        story.append(Paragraph("INFORMATIONS MATCH", styles["Heading1"]))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Détails du match", styles["Heading2"]))
        match_data = [
            ["Équipe domicile", self._get_value(data, "home_team")],
            ["Équipe extérieur", self._get_value(data, "away_team")],
            ["Date", self._get_value(data, "date")],
            ["Heure", self._get_value(data, "time")],
            ["Stade", self._get_value(data, "venue")],
            ["Compétition", self._get_value(data, "league")],
            ["Statut", self._get_value(data, "status")],
            ["Arbitre", self._get_value(data, "referee")],
        ]
        story.append(self._create_info_table(match_data))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("Traçabilité des données", styles["Heading2"]))
        sources, source_count, consensus = self._extract_fusion_meta(data)
        meta_data = [
            ["Sources consultées", ", ".join(sources) if sources else "N/A"],
            ["Nombre de sources", str(source_count) if source_count is not None else "N/A"],
            ["Consensus", consensus or "N/A"],
            ["Qualité des données", self._assess_data_quality(data)],
        ]
        story.append(self._create_info_table(meta_data))

        return story

    def _build_generic_report(self, entity_type: str, data: dict[str, Any]) -> list[Any]:
        styles = self._get_styles()
        story: list[Any] = [Paragraph(f"RAPPORT {entity_type.upper()}", styles["Heading1"]), Spacer(1, 5 * mm)]
        # Afficher un sous-ensemble lisible
        rows: list[list[str]] = []
        for k, v in data.items():
            if str(k).startswith("_"):
                continue
            rows.append([str(k), str(v) if v is not None else "N/A"])
        story.append(self._create_info_table(rows[:30] or [["Données", "N/A"]]))
        return story

    def _build_metadata_page(self, data: dict[str, Any]) -> list[Any]:
        story: list[Any] = []
        styles = self._get_styles()

        story.append(Paragraph("MÉTADONNÉES DU RAPPORT", styles["Heading1"]))
        story.append(Spacer(1, 5 * mm))

        sources, source_count, consensus = self._extract_fusion_meta(data)

        meta = [
            ["Version du système", f"LG Sport V{self.system_version}"],
            ["Date de génération", datetime.now().strftime("%d/%m/%Y %H:%M:%S")],
            ["Type de rapport", str(data.get("entity_type", "N/A")).capitalize()],
            ["Sources de données", ", ".join(sources) or "N/A"],
            ["Nombre de sources", str(source_count) if source_count is not None else "N/A"],
            ["Consensus", consensus or "N/A"],
        ]

        story.append(self._create_info_table(meta))
        return story

    def _build_charts(self, data: dict[str, Any]) -> list[Any]:
        styles = self._get_styles()
        charts = data.get("charts")
        if not isinstance(charts, list) or not charts:
            return []

        story: list[Any] = [Paragraph("GRAPHIQUES", styles["Heading1"]), Spacer(1, 3 * mm)]

        # Limiter à 3 graphiques dans le PDF
        for ch in charts[:3]:
            if not isinstance(ch, dict):
                continue
            title = str(ch.get("title") or "Graphique")
            ctype = str(ch.get("type") or "bar")
            labels = ch.get("labels") if isinstance(ch.get("labels"), list) else []
            datasets = ch.get("datasets") if isinstance(ch.get("datasets"), list) else []
            if not datasets:
                continue

            ds0 = datasets[0] if isinstance(datasets[0], dict) else {}
            values = ds0.get("data") if isinstance(ds0.get("data"), list) else []
            if not labels or not values:
                continue

            story.append(Paragraph(title, styles["Heading2"]))
            story.append(Spacer(1, 2 * mm))

            if ctype in {"doughnut", "pie"}:
                drawing = Drawing(170 * mm, 55 * mm)
                pie = Pie()
                pie.x = 10
                pie.y = 5
                pie.width = int(55 * mm)
                pie.height = int(55 * mm)
                try:
                    pie.data = [float(v) for v in values]
                except Exception:
                    pie.data = [0.0 for _ in values]
                pie.labels = [str(label) for label in labels]
                pie.slices.strokeWidth = 0.5
                drawing.add(pie)
                drawing.add(
                    String(
                        70 * mm,
                        40 * mm,
                        " / ".join([str(label) for label in labels[:3]]),
                        fontSize=8,
                        fillColor=self.colors["text_light"],
                    )
                )
                story.append(drawing)
            else:
                drawing = Drawing(170 * mm, 70 * mm)
                chart = VerticalBarChart()
                chart.x = 10
                chart.y = 10
                chart.height = int(55 * mm)
                chart.width = int(160 * mm)
                try:
                    chart.data = [[float(v) for v in values]]
                except Exception:
                    chart.data = [[0.0 for _ in values]]
                chart.categoryAxis.categoryNames = [str(label) for label in labels]
                chart.valueAxis.valueMin = 0
                chart.bars[0].fillColor = self.colors["primary"]
                drawing.add(chart)
                story.append(drawing)

            story.append(Spacer(1, 6 * mm))

        return story

    def _create_info_table(self, data: list[list[str]]) -> Table:
        table = Table(data, colWidths=[60 * mm, 110 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), self.colors["primary"]),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("BACKGROUND", (1, 0), (1, -1), colors.white),
                    ("TEXTCOLOR", (1, 0), (1, -1), self.colors["text"]),
                    ("ALIGN", (1, 0), (1, -1), "LEFT"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
                    ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
                    ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ]
            )
        )
        return table

    def _get_styles(self) -> Any:
        styles = getSampleStyleSheet()

        # ReportLab fournit déjà Title/Heading1/Heading2. On les ajuste au lieu de les ré-ajouter.
        title = styles["Title"]
        title.fontSize = 24
        title.textColor = self.colors["primary"]
        title.spaceAfter = 10 * mm
        title.alignment = 1
        title.fontName = "Helvetica-Bold"

        h1 = styles["Heading1"]
        h1.fontSize = 16
        h1.textColor = self.colors["primary"]
        h1.spaceAfter = 5 * mm
        h1.spaceBefore = 5 * mm
        h1.fontName = "Helvetica-Bold"

        h2 = styles["Heading2"]
        h2.fontSize = 12
        h2.textColor = self.colors["secondary"]
        h2.spaceAfter = 3 * mm
        h2.spaceBefore = 3 * mm
        h2.fontName = "Helvetica-Bold"

        return styles

    def _get_entity_name(self, data: dict[str, Any], entity_type: str) -> str:
        if entity_type == "player":
            for key in ("full_name", "name", "player_name", "strPlayer"):
                val = data.get(key)
                if isinstance(val, dict):
                    val = val.get("value")
                if val:
                    return str(val)
            return "Joueur Inconnu"

        if entity_type == "team":
            for key in ("name", "team_name", "strTeam", "short_name"):
                val = data.get(key)
                if isinstance(val, dict):
                    val = val.get("value")
                if val:
                    return str(val)
            return "Équipe Inconnue"

        if entity_type == "match":
            home = self._get_value(data, "home_team")
            away = self._get_value(data, "away_team")
            return f"{home} vs {away}"

        return "Inconnu"

    def _get_value(self, data: dict[str, Any], key: str) -> str:
        field = data.get(key)

        if isinstance(field, dict):
            value = field.get("value")
            consensus = field.get("consensus")
            if value:
                return f"{value} ({consensus})" if consensus else str(value)
            return "N/A"

        return str(field) if field not in (None, "") else "N/A"

    def _extract_fusion_meta(self, data: dict[str, Any]) -> tuple[list[str], int | None, str | None]:
        if isinstance(data.get("_fusion_metadata"), dict):
            meta = data["_fusion_metadata"]
            sources = meta.get("sources")
            if not isinstance(sources, list):
                sources = []
            source_count = meta.get("source_count")
            if isinstance(source_count, int):
                sc = source_count
            else:
                sc = None
            consensus = meta.get("consensus_level")
            return [str(s) for s in sources], sc, str(consensus) if consensus else None

        sources = data.get("sources")
        if not isinstance(sources, list):
            sources = []

        sc_raw = data.get("source_count")
        sc = sc_raw if isinstance(sc_raw, int) else None

        consensus = data.get("consensus")
        return [str(s) for s in sources], sc, str(consensus) if consensus else None

    def _assess_data_quality(self, data: dict[str, Any]) -> str:
        _, sc, _ = self._extract_fusion_meta(data)
        if sc is None:
            return "Non évaluée"
        if sc >= 4:
            return "Excellente"
        if sc >= 2:
            return "Bonne"
        if sc == 1:
            return "Moyenne"
        return "Non évaluée"


__all__ = ["PDFGenerator"]
