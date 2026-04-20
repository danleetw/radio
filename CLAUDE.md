# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案說明

純 HTML/CSS/JavaScript 無線電技術實作展示專案，無需建置工具或伺服器。所有實作直接在瀏覽器執行，利用 Web API（AudioContext、Web Audio API、Canvas 等）模擬無線電技術原理。

## 開發方式

直接用瀏覽器開啟 `.html` 檔案即可執行，無需 npm、建置步驟或後端。

## 技術約束

- 純 HTML + CSS + JavaScript，無框架、無相依套件
- 不使用 CDN 外部資源（除非明確指定）
- 音訊處理：Web Audio API（`AudioContext`、`OscillatorNode`、`AnalyserNode` 等）
- 視覺化：Canvas 2D API 或 SVG
- 每個實作為獨立的單一 `.html` 檔案

## 無線電技術範圍

AM/FM 調變解調、頻譜分析、FFT 視覺化、濾波器模擬、SDR 概念展示等。
