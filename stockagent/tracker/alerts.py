"""信号提醒规则库(九条 MVP)— Phase 1-A 填充。定位 B 的「心脏」。

双通道(PRD §8):alerts 是独立规则库,看板顶部告警区 + 微信推送 两个消费者同源
(推送失败时看板兜底)。

九条 MVP(PRD §8.2):
  D  筹码相位转兑现中段/见顶预警(空)、见底/低位加仓(多)
  E1 60日线有效突破(收盘+2/3%)→ 右侧买点
  E2 60日线有效跌破(收盘-2/3%)→ 止盈止损
  C1 周期 PB 触历史低位(<10%)→ 底部区域
  C2 周期 PB 触历史高位(>90%)→ 顶部警告
  B1 价值股息率达历史高位(PE 分位极低)→ 买入窗口
  A1 业绩预告 yoy 较上期增速下滑(拐点)→ 抱着颗雷
  A2 预告类型转空(预增→预减/首亏)→ 戴维斯双杀前兆
  F1 大盘(沪深300)60日线趋势反转 → 风险开关

Phase 1-B 的指数层突破跌破信号已由 diagnose.breakout_grade 暴露(⑤),alerts 在
Phase 1-A 随 ETF 三类分类一起接入双通道。
"""
from __future__ import annotations


def evaluate(*args, **kwargs) -> list:
    """占位 — Phase 1-A 实现。返回 Alert 列表(看板告警区 + 微信推送同源消费)。"""
    # TODO(Phase 1-A): evaluate(diag_layer, diag_etf) -> list[Alert]
    return []
