#!/usr/bin/env python3
"""Pins for kalshi_settlement_pnl.py -- the settlement P&L attributor.

FIXTURES ARE LIVE DATA, not invented: SETTLEMENT_ROWS is the complete
GET /portfolio/settlements response of 2026-07-23 (all 51 settled contracts, verbatim), and
FILL_ROWS is the verbatim fill tape of 5 selected contracts. Three of those five
(KXAAAGASW-26JUL27-4.100/-4.120/-4.160) are still OPEN, so the venue's own
`realized_pnl_dollars` and `position_fp` are known for them and are pinned here as ground truth
for the replay model. Do not "tidy" these numbers.

PIN DISCIPLINE: `naive_row_pnl` in the module is the PRE-FIX formula (cumulative lifetime cost
read as position cost). Every test below that asserts a P&L is written so that it FAILS if the
correct model is replaced by that naive one -- see test_naive_formula_fails_every_pnl_pin, which
measures that property instead of asserting it in prose.
"""
import os

import pytest

import kalshi_settlement_pnl as SP

HERE = os.path.dirname(os.path.abspath(__file__))

# ticker, market_result, yes_count_fp, no_count_fp, yes_total_cost, no_total_cost,
# revenue(CENTS), fee_cost, settled_time
_S = [
    ('KXAAAGASD-26JUL21-4.010', 'yes', 28.00, 28.00, 26.320000, 2.720000, 0, 0.102600, '2026-07-21T11:40:19.811742Z'),
    ('KXAAAGASD-26JUL21-4.015', 'yes', 20.00, 20.00, 17.400000, 3.600000, 0, 0.000000, '2026-07-21T11:40:19.811742Z'),
    ('KXAAAGASD-26JUL21-4.020', 'no', 50.71, 51.00, 33.634400, 18.460000, 29, 0.221800, '2026-07-21T11:40:19.811403Z'),
    ('KXAAAGASD-26JUL21-4.025', 'no', 20.00, 20.12, 10.180000, 10.462400, 12, 0.349900, '2026-07-21T11:40:19.811403Z'),
    ('KXAAAGASD-26JUL21-4.030', 'no', 20.00, 19.67, 3.800000, 16.626100, 0, 0.180400, '2026-07-21T11:40:19.811403Z'),
    ('KXAAAGASD-26JUL22-4.055', 'yes', 7.00, 7.08, 6.230000, 0.920400, 0, 0.000000, '2026-07-22T12:05:43.962469Z'),
    ('KXAAAGASD-26JUL23-4.090', 'yes', 16.11, 38.00, 13.291300, 7.040000, 0, 0.000000, '2026-07-23T12:25:36.016176Z'),
    ('KXAAAGASD-26JUL23-4.095', 'no', 157.04, 157.10, 108.679200, 46.557500, 6, 0.000000, '2026-07-23T12:25:36.016176Z'),
    ('KXAAAGASD-26JUL23-4.100', 'no', 233.52, 233.75, 83.462100, 151.245500, 23, 0.000000, '2026-07-23T12:25:36.016176Z'),
    ('KXAAAGASD-26JUL23-4.105', 'no', 56.00, 28.00, 11.530000, 20.620000, 0, 0.000000, '2026-07-23T12:25:36.016176Z'),
    ('KXTEMPAUSH-26JUL2015-T91.99', 'yes', 20.00, 20.00, 19.800000, 1.400000, 0, 0.013900, '2026-07-20T20:20:49.804907Z'),
    ('KXTEMPAUSH-26JUL2015-T92.99', 'yes', 19.77, 20.00, 19.179200, 2.800000, 0, 0.040100, '2026-07-20T20:20:49.804903Z'),
    ('KXTEMPAUSH-26JUL2021-T88.99', 'yes', 20.00, 20.00, 19.600000, 2.200000, 0, 0.027500, '2026-07-21T02:30:59.803387Z'),
    ('KXTEMPAUSH-26JUL2021-T89.99', 'yes', 19.18, 20.00, 16.544800, 2.800000, 0, 0.159200, '2026-07-21T02:30:59.803382Z'),
    ('KXTEMPAUSH-26JUL2021-T90.99', 'no', 48.00, 48.00, 41.680000, 6.520000, 0, 0.384400, '2026-07-21T02:30:59.803387Z'),
    ('KXTEMPAUSH-26JUL2021-T91.99', 'no', 16.00, 16.00, 13.760000, 4.000000, 0, 0.210000, '2026-07-21T02:30:59.803387Z'),
    ('KXTEMPAUSH-26JUL2021-T92.99', 'no', 21.00, 21.00, 14.870000, 12.600000, 0, 0.161800, '2026-07-21T02:30:59.803387Z'),
    ('KXTEMPAUSH-26JUL2021-T93.99', 'no', 20.00, 20.00, 1.200000, 18.800000, 0, 0.079100, '2026-07-21T02:30:59.803387Z'),
    ('KXTEMPAUSH-26JUL2021-T94.99', 'no', 40.00, 40.00, 4.800000, 39.200000, 0, 0.055000, '2026-07-21T02:30:59.803387Z'),
    ('KXTEMPAUSH-26JUL2123-T82.99', 'no', 17.00, 13.00, 7.480000, 7.150000, 0, 0.000000, '2026-07-22T04:31:04.00079Z'),
    ('KXTEMPAUSH-26JUL2123-T83.99', 'no', 1.00, 3.93, 0.290000, 2.751000, 293, 0.000000, '2026-07-22T04:31:04.00079Z'),
    ('KXTEMPAUSH-26JUL2123-T84.99', 'no', 35.00, 20.00, 3.600000, 18.120000, 0, 0.000000, '2026-07-22T04:31:04.00079Z'),
    ('KXTEMPAUSH-26JUL2207-T77.99', 'no', 8.00, 7.02, 6.720000, 1.203000, 0, 0.000000, '2026-07-22T12:30:54.00091Z'),
    ('KXTEMPAUSH-26JUL2208-T75.99', 'yes', 18.00, 18.00, 13.950000, 4.320000, 0, 0.000000, '2026-07-22T13:31:14.060561Z'),
    ('KXTEMPAUSH-26JUL2212-T95.99', 'yes', 3.00, 3.78, 2.640000, 0.529200, 0, 0.000000, '2026-07-22T17:30:54.00742Z'),
    ('KXTEMPCHIH-26JUL2021-T76.99', 'yes', 20.00, 20.00, 19.510000, 2.400000, 0, 0.033600, '2026-07-21T02:30:59.802513Z'),
    ('KXTEMPCHIH-26JUL2021-T77.99', 'yes', 8.00, 8.00, 6.960000, 1.360000, 0, 0.079200, '2026-07-21T02:30:59.802513Z'),
    ('KXTEMPCHIH-26JUL2123-T69.99', 'yes', 6.00, 21.00, 4.800000, 3.990000, 0, 0.000000, '2026-07-22T04:31:03.999438Z'),
    ('KXTEMPCHIH-26JUL2123-T70.99', 'no', 18.00, 10.00, 12.200000, 7.700000, 0, 0.000000, '2026-07-22T04:31:03.999438Z'),
    ('KXTEMPCHIH-26JUL2206-T58.99', 'yes', 0.00, 20.00, 0.000000, 1.400000, 0, 0.000000, '2026-07-22T11:30:43.955752Z'),
    ('KXTEMPCHIH-26JUL2206-T59.99', 'yes', 15.00, 17.00, 14.400000, 0.850000, 0, 0.000000, '2026-07-22T11:30:43.955752Z'),
    ('KXTEMPCHIH-26JUL2207-T59.99', 'yes', 0.00, 20.00, 0.000000, 2.800000, 0, 0.000000, '2026-07-22T12:30:54.006431Z'),
    ('KXTEMPCHIH-26JUL2208-T59.99', 'yes', 0.00, 20.00, 0.000000, 2.200000, 0, 0.000000, '2026-07-22T13:31:14.057575Z'),
    ('KXTEMPCHIH-26JUL2211-T66.99', 'yes', 0.00, 20.00, 0.000000, 2.400000, 0, 0.000000, '2026-07-22T16:30:54.005458Z'),
    ('KXTEMPCHIH-26JUL2211-T67.99', 'yes', 20.00, 20.00, 15.270000, 4.800000, 0, 0.000000, '2026-07-22T16:30:54.005468Z'),
    ('KXTEMPCHIH-26JUL2212-T69.99', 'no', 31.85, 20.00, 21.413500, 5.600000, 0, 0.000000, '2026-07-22T17:30:54.006602Z'),
    ('KXTEMPDCH-26JUL2021-T78.99', 'yes', 20.00, 20.00, 19.390000, 5.000000, 0, 0.041500, '2026-07-21T02:30:59.801627Z'),
    ('KXTEMPDCH-26JUL2021-T79.99', 'yes', 20.00, 20.00, 19.590000, 5.800000, 0, 0.028200, '2026-07-21T02:30:59.801627Z'),
    ('KXTEMPDCH-26JUL2021-T80.99', 'yes', 20.31, 20.00, 19.378100, 5.800000, 31, 0.060200, '2026-07-21T02:30:59.801621Z'),
    ('KXTEMPDCH-26JUL2123-T72.99', 'yes', 8.00, 0.00, 6.800000, 0.000000, 800, 0.000000, '2026-07-22T04:31:04.002899Z'),
    ('KXTEMPDCH-26JUL2123-T73.99', 'yes', 39.00, 17.00, 16.260000, 14.450000, 2200, 0.000000, '2026-07-22T04:31:04.002899Z'),
    ('KXTEMPDCH-26JUL2206-T71.99', 'yes', 0.00, 20.00, 0.000000, 1.000000, 0, 0.000000, '2026-07-22T11:30:54.000329Z'),
    ('KXTEMPLAXH-26JUL2021-T74.99', 'yes', 14.00, 15.00, 12.740000, 1.350000, 0, 0.080400, '2026-07-21T02:30:59.800779Z'),
    ('KXTEMPLAXH-26JUL2021-T75.99', 'no', 12.00, 12.00, 7.440000, 6.720000, 0, 0.207000, '2026-07-21T02:30:59.800795Z'),
    ('KXTEMPLAXH-26JUL2211-T69.99', 'no', 40.00, 40.00, 34.080000, 6.000000, 0, 0.000000, '2026-07-22T16:30:54.007358Z'),
    ('KXTEMPLAXH-26JUL2211-T70.99', 'no', 20.00, 20.00, 5.600000, 14.800000, 0, 0.000000, '2026-07-22T16:30:54.007358Z'),
    ('KXTEMPLAXH-26JUL2212-T70.99', 'no', 2.91, 2.00, 2.211600, 0.460000, 0, 0.000000, '2026-07-22T17:30:54.003688Z'),
    ('KXTEMPLAXH-26JUL2212-T71.99', 'no', 15.00, 0.00, 7.200000, 0.000000, 0, 0.000000, '2026-07-22T17:30:54.003688Z'),
    ('KXTEMPNYCH-26JUL2206-T69.99', 'yes', 0.00, 20.00, 0.000000, 2.000000, 0, 0.000000, '2026-07-22T11:30:43.954929Z'),
    ('KXTEMPNYCH-26JUL2211-T78.99', 'yes', 19.00, 19.00, 11.610000, 7.410000, 0, 0.000000, '2026-07-22T16:30:54.004572Z'),
    ('KXTEMPNYCH-26JUL2212-T80.99', 'yes', 3.55, 20.00, 3.159500, 2.000000, 0, 0.000000, '2026-07-22T17:30:54.005707Z'),
]

# ticker, created_time, action, count_fp, yes_price_dollars, fee_cost
_F = [
    ('KXAAAGASD-26JUL23-4.090', '2026-07-22T18:32:30.932339Z', 'buy', 8.11, 0.8300, 0.000000),
    ('KXAAAGASD-26JUL23-4.090', '2026-07-22T18:39:13.745656Z', 'buy', 8.00, 0.8200, 0.000000),
    ('KXAAAGASD-26JUL23-4.090', '2026-07-22T20:22:04.428768Z', 'sell', 16.00, 0.8200, 0.000000),
    ('KXAAAGASD-26JUL23-4.090', '2026-07-22T20:46:31.641804Z', 'sell', 20.00, 0.8000, 0.000000),
    ('KXAAAGASD-26JUL23-4.090', '2026-07-23T01:04:45.78426Z', 'sell', 2.00, 0.9200, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T18:32:26.822695Z', 'buy', 7.04, 0.7300, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T18:45:37.342213Z', 'sell', 7.00, 0.7300, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T18:53:23.858504Z', 'buy', 9.00, 0.7300, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T19:03:51.577976Z', 'buy', 10.00, 0.7300, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T19:04:01.289072Z', 'sell', 9.00, 0.7400, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T19:39:46.339985Z', 'sell', 10.00, 0.7300, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T20:27:17.061642Z', 'sell', 0.65, 0.7400, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T20:30:08.808257Z', 'buy', 3.00, 0.7200, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T20:31:50.16545Z', 'buy', 2.00, 0.7000, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T22:01:12.619895Z', 'sell', 2.00, 0.7000, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-22T22:28:16.046551Z', 'sell', 2.00, 0.7000, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T00:40:52.457355Z', 'sell', 20.00, 0.7300, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T00:44:25.122533Z', 'buy', 19.00, 0.7100, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T00:45:48.223077Z', 'sell', 20.00, 0.7100, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:00:52.771902Z', 'buy', 1.34, 0.7000, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:01:40.651993Z', 'buy', 18.66, 0.7000, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:02:35.751417Z', 'sell', 19.00, 0.7000, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:08:15.741766Z', 'buy', 19.00, 0.6900, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:08:22.351078Z', 'sell', 0.71, 0.7100, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:09:29.067445Z', 'sell', 1.29, 0.7100, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:13:11.363287Z', 'buy', 2.00, 0.6500, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:15:38.786328Z', 'buy', 4.00, 0.5800, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:24:37.910191Z', 'sell', 3.00, 0.6200, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:27:07.600888Z', 'sell', 16.00, 0.6800, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:29:31.734101Z', 'buy', 15.00, 0.6800, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:30:01.691306Z', 'sell', 2.00, 0.7100, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:31:19.961803Z', 'buy', 2.00, 0.6600, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:32:07.281441Z', 'sell', 1.45, 0.6700, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:38:01.46236Z', 'sell', 2.00, 0.6700, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:38:48.156442Z', 'buy', 1.09, 0.6700, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:38:49.136574Z', 'buy', 2.91, 0.6700, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:40:19.932445Z', 'sell', 15.00, 0.7000, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:43:52.999464Z', 'buy', 2.00, 0.6900, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:43:52.999464Z', 'buy', 15.00, 0.6900, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:46:36.108392Z', 'sell', 14.00, 0.6900, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:51:53.540224Z', 'buy', 12.00, 0.6800, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T01:58:33.701984Z', 'sell', 12.00, 0.6700, 0.000000),
    ('KXAAAGASD-26JUL23-4.095', '2026-07-23T02:01:31.106285Z', 'buy', 12.00, 0.6500, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T14:54:05.987047Z', 'buy', 14.00, 0.5200, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T15:11:51.483363Z', 'sell', 14.00, 0.5000, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T16:49:00.840625Z', 'sell', 12.00, 0.4200, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T17:18:52.413284Z', 'buy', 3.46, 0.4400, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T17:34:51.975091Z', 'buy', 8.00, 0.4300, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T18:32:30.721378Z', 'buy', 6.70, 0.4400, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T18:33:52.282164Z', 'sell', 6.00, 0.4500, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T18:53:57.159473Z', 'sell', 12.00, 0.4200, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T19:19:40.419965Z', 'buy', 6.03, 0.4700, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T19:19:59.87433Z', 'buy', 5.48, 0.4700, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T19:25:48.93079Z', 'buy', 14.00, 0.4700, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T19:42:56.326543Z', 'buy', 17.00, 0.3700, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T21:23:24.989613Z', 'sell', 2.87, 0.4000, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T21:50:14.753656Z', 'sell', 12.00, 0.4000, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T21:52:54.540008Z', 'sell', 2.00, 0.4300, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T21:52:54.540008Z', 'sell', 15.00, 0.4300, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T21:53:30.219742Z', 'sell', 1.28, 0.4300, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T21:54:55.367683Z', 'buy', 19.00, 0.3800, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-22T21:55:22.609415Z', 'buy', 19.00, 0.3200, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:23:13.504444Z', 'sell', 11.00, 0.3300, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:26:52.797019Z', 'sell', 12.29, 0.3900, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:38:09.193064Z', 'sell', 12.00, 0.3500, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:38:47.655932Z', 'buy', 2.85, 0.3200, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:42:21.57458Z', 'buy', 20.00, 0.3400, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:47:37.754874Z', 'sell', 22.00, 0.3300, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:50:19.306434Z', 'sell', 1.00, 0.3600, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:52:42.63519Z', 'buy', 20.00, 0.3200, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T00:54:55.644978Z', 'sell', 20.00, 0.3100, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:00:43.079443Z', 'sell', 10.00, 0.3400, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:02:36.541213Z', 'sell', 10.00, 0.3600, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:09:54.229741Z', 'buy', 19.00, 0.3500, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:10:35.458606Z', 'buy', 18.00, 0.3100, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:13:59.814319Z', 'buy', 15.00, 0.2400, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:18:54.071732Z', 'sell', 20.00, 0.2700, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:21:09.644981Z', 'sell', 12.00, 0.2800, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:27:57.832324Z', 'sell', 8.00, 0.2700, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:29:42.757499Z', 'sell', 2.00, 0.2900, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:35:09.423905Z', 'sell', 2.00, 0.2800, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:37:07.37631Z', 'sell', 0.31, 0.3000, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:38:08.547028Z', 'buy', 11.00, 0.2700, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:39:27.41395Z', 'sell', 6.00, 0.2100, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:44:20.398904Z', 'buy', 3.00, 0.2400, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:52:51.333725Z', 'sell', 2.00, 0.2500, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:54:48.976265Z', 'buy', 6.00, 0.2400, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T01:58:37.774303Z', 'sell', 6.00, 0.2600, 0.000000),
    ('KXAAAGASD-26JUL23-4.100', '2026-07-23T02:10:28.983768Z', 'buy', 6.00, 0.2700, 0.000000),
    ('KXAAAGASD-26JUL23-4.105', '2026-07-22T16:47:28.758589Z', 'sell', 10.00, 0.3100, 0.000000),
    ('KXAAAGASD-26JUL23-4.105', '2026-07-22T17:00:37.071736Z', 'buy', 10.00, 0.3000, 0.000000),
    ('KXAAAGASD-26JUL23-4.105', '2026-07-22T19:18:30.5707Z', 'sell', 10.00, 0.2600, 0.000000),
    ('KXAAAGASD-26JUL23-4.105', '2026-07-22T19:42:41.107117Z', 'buy', 17.00, 0.2600, 0.000000),
    ('KXAAAGASD-26JUL23-4.105', '2026-07-22T20:23:33.865527Z', 'buy', 9.00, 0.1900, 0.000000),
    ('KXAAAGASD-26JUL23-4.105', '2026-07-22T20:42:52.722596Z', 'sell', 8.00, 0.2100, 0.000000),
    ('KXAAAGASD-26JUL23-4.105', '2026-07-22T21:55:15.651129Z', 'buy', 20.00, 0.1200, 0.000000),
    ('KXAAAGASW-26JUL27-4.100', '2026-07-22T04:48:38.15333Z', 'sell', 20.00, 0.9100, 0.000000),
    ('KXAAAGASW-26JUL27-4.100', '2026-07-22T08:34:00.975304Z', 'buy', 16.00, 0.9300, 0.000000),
    ('KXAAAGASW-26JUL27-4.100', '2026-07-22T08:39:40.75779Z', 'buy', 4.00, 0.9000, 0.000000),
    ('KXAAAGASW-26JUL27-4.100', '2026-07-22T14:52:54.464896Z', 'buy', 8.00, 0.9000, 0.000000),
    ('KXAAAGASW-26JUL27-4.100', '2026-07-22T15:21:36.258627Z', 'sell', 8.00, 0.9000, 0.000000),
    ('KXAAAGASW-26JUL27-4.120', '2026-07-22T13:46:23.801908Z', 'buy', 8.00, 0.8600, 0.000000),
    ('KXAAAGASW-26JUL27-4.120', '2026-07-22T14:16:36.919104Z', 'sell', 1.29, 0.8400, 0.000000),
    ('KXAAAGASW-26JUL27-4.120', '2026-07-22T15:09:29.039704Z', 'sell', 6.71, 0.8400, 0.000000),
    ('KXAAAGASW-26JUL27-4.120', '2026-07-22T19:19:24.082944Z', 'sell', 17.00, 0.9500, 0.000000),
    ('KXAAAGASW-26JUL27-4.120', '2026-07-23T03:38:05.986287Z', 'buy', 15.00, 0.9400, 0.000000),
    ('KXAAAGASW-26JUL27-4.120', '2026-07-23T05:36:21.69524Z', 'buy', 2.00, 0.9400, 0.000000),
    ('KXAAAGASW-26JUL27-4.160', '2026-07-23T01:40:02.76136Z', 'sell', 1.00, 0.3000, 0.000000),
    ('KXAAAGASW-26JUL27-4.160', '2026-07-23T02:16:16.341263Z', 'sell', 5.00, 0.3000, 0.000000),
    ('KXAAAGASW-26JUL27-4.160', '2026-07-23T02:20:04.896545Z', 'sell', 3.00, 0.3100, 0.000000),
    ('KXAAAGASW-26JUL27-4.160', '2026-07-23T02:26:41.479645Z', 'sell', 1.00, 0.3100, 0.000000),
    ('KXAAAGASW-26JUL27-4.160', '2026-07-23T14:01:02.38735Z', 'buy', 5.00, 0.3200, 0.000000),
    ('KXAAAGASW-26JUL27-4.160', '2026-07-23T14:30:15.645405Z', 'sell', 8.00, 0.3300, 0.000000),
]

SETTLEMENT_ROWS = [
    {"ticker": t, "event_ticker": SP.event_of(t), "market_result": res,
     "yes_count_fp": "%.2f" % y, "no_count_fp": "%.2f" % n,
     "yes_total_cost_dollars": "%.6f" % yc, "no_total_cost_dollars": "%.6f" % nc,
     "revenue": rev, "value": 0, "fee_cost": "%.6f" % fee, "settled_time": st}
    for (t, res, y, n, yc, nc, rev, fee, st) in _S
]

FILL_ROWS = [
    {"ticker": t, "created_time": ct, "fill_id": "%s-%03d" % (t, i), "action": act,
     "side": "yes" if act == "buy" else "no", "count_fp": "%.2f" % c,
     "yes_price_dollars": "%.4f" % p, "no_price_dollars": "%.4f" % (1.0 - p),
     "fee_cost": "%.6f" % fee}
    for i, (t, ct, act, c, p, fee) in enumerate(_F)
]

BY_TICKER = SP.group_fills(FILL_ROWS)
ROW = {r["ticker"]: r for r in SETTLEMENT_ROWS}

GASD_EVENT = "KXAAAGASD-26JUL23"
GASD_TICKERS = [t for t in ROW if t.startswith(GASD_EVENT + "-")]

# VENUE GROUND TRUTH -- GET /portfolio/positions, 2026-07-23. realized_pnl_dollars is computed by
# Kalshi, not by us. These are the only rows in this file we did not derive.
VENUE_POSITIONS = {
    "KXAAAGASW-26JUL27-4.100": {"position_fp": 0.0, "realized_pnl_dollars": -0.28},
    "KXAAAGASW-26JUL27-4.120": {"position_fp": 0.0, "realized_pnl_dollars": 0.01},
    "KXAAAGASW-26JUL27-4.160": {"position_fp": -13.0, "realized_pnl_dollars": -0.08},
}

# Account size on 2026-07-23. Used ONLY as an order-of-magnitude sanity bound: a settlement P&L
# many multiples of the whole account is arithmetically impossible, not "a bad day".
ACCOUNT_SIZE_USD = 85.0

CENT = SP.CENT


def _mk(ticker, result, y, n, yc, nc, rev_cents, fee=0.0):
    return {"ticker": ticker, "market_result": result,
            "yes_count_fp": str(y), "no_count_fp": str(n),
            "yes_total_cost_dollars": str(yc), "no_total_cost_dollars": str(nc),
            "revenue": rev_cents, "fee_cost": str(fee)}


def _fill(ticker, i, action, count, yes_price, fee=0.0):
    return {"ticker": ticker, "created_time": "2026-07-23T00:00:%02dZ" % i, "fill_id": str(i),
            "action": action, "side": "yes" if action == "buy" else "no",
            "count_fp": str(count), "yes_price_dollars": "%.4f" % yes_price,
            "no_price_dollars": "%.4f" % (1.0 - yes_price), "fee_cost": str(fee)}


# ============================================================================================
# PIN BODIES. Each _pin_* is the actual assertion set; the test_* wrappers below run them, and
# the meta-tests re-run them against the PRE-FIX formulas to MEASURE how many are real pins.
# ============================================================================================
def _pin_revenue_is_cents():
    assert SP.revenue_dollars(ROW["KXAAAGASD-26JUL23-4.100"]) == pytest.approx(0.23, abs=1e-9)
    assert SP.revenue_dollars({"revenue": 3000}) == pytest.approx(30.0, abs=1e-9)


def _pin_counts_are_cumulative_not_net():
    r = ROW["KXAAAGASD-26JUL23-4.100"]
    assert SP._f(r["yes_count_fp"]) == pytest.approx(233.52)
    assert SP._f(r["no_count_fp"]) == pytest.approx(233.75)
    # the whole -$442 error in one assertion: gross is >1000x the position
    assert abs(SP.net_position(r)) == pytest.approx(0.23, abs=CENT)
    assert SP._f(r["yes_count_fp"]) / abs(SP.net_position(r)) > 1000


def _pin_net_position_is_yes_signed():
    assert SP.net_position(ROW["KXAAAGASD-26JUL23-4.105"]) == pytest.approx(28.0, abs=CENT)
    assert SP.net_position(ROW["KXAAAGASD-26JUL23-4.090"]) == pytest.approx(-21.89, abs=CENT)


def _pin_documented_row_reconciles():
    r = ROW["KXAAAGASD-26JUL23-4.100"]
    assert r["market_result"] == "no"
    assert SP.expected_revenue_dollars(r) == pytest.approx(0.23, abs=1e-9)
    assert SP.revenue_dollars(r) == pytest.approx(0.23, abs=1e-9)
    assert SP.revenue_reconciles(r)


def _pin_revenue_zero_when_net_is_on_the_losing_side():
    r = ROW["KXAAAGASD-26JUL23-4.105"]     # net +28 YES into a "no" result
    assert SP.expected_revenue_dollars(r) == 0.0
    assert SP.revenue_dollars(r) == 0.0
    assert SP.revenue_reconciles(r)


def _pin_revenue_reconciles_on_every_row():
    ok, tot, bad = SP.reconcile_revenue(SETTLEMENT_ROWS)
    assert tot == 51
    assert bad == []
    assert ok == 51


def _pin_naive_reproduces_the_impossible_442():
    naive = sum(SP.naive_row_pnl(ROW[t]) for t in GASD_TICKERS)
    assert naive == pytest.approx(-442.1356, abs=0.01)
    assert abs(naive) > 5 * ACCOUNT_SIZE_USD      # impossible => the formula is wrong


def _pin_correct_model_is_physically_possible():
    pnl = sum(SP.settlement_row_pnl(ROW[t]) for t in GASD_TICKERS)
    assert abs(pnl) < ACCOUNT_SIZE_USD


def _pin_gasd_event_lifetime_pnl():
    pnl = sum(SP.settlement_row_pnl(ROW[t]) for t in GASD_TICKERS)
    assert len(GASD_TICKERS) == 4
    assert pnl == pytest.approx(-7.4656, abs=0.01)


def _pin_gasd_settlement_leg_and_basis():
    """Reproduces the independent lane's -$8.20: it is the SETTLEMENT LEG, not the lifetime."""
    basis = leg = 0.0
    for t in GASD_TICKERS:
        pos, avg, _real, _fee = SP.replay(BY_TICKER.get(t, []))
        basis += SP.carried_cost_basis(pos, avg)
        leg += SP.settlement_leg_pnl(pos, avg, ROW[t]["market_result"])
    assert basis == pytest.approx(8.4932, abs=0.01)
    assert leg == pytest.approx(-8.2032, abs=0.01)


def _pin_leg_plus_realized_equals_lifetime():
    n = 0
    for t in GASD_TICKERS:
        fl = BY_TICKER.get(t, [])
        if not fl:
            continue
        n += 1
        pos, avg, real, fee = SP.replay(fl)
        lifetime = real + SP.settlement_leg_pnl(pos, avg, ROW[t]["market_result"]) - fee
        assert lifetime == pytest.approx(SP.settlement_row_pnl(ROW[t]), abs=CENT), t
    assert n == 4


def _pin_two_models_agree_on_every_taped_contract():
    n = 0
    for t, fl in BY_TICKER.items():
        if t not in ROW:
            continue
        n += 1
        assert SP.models_agree(ROW[t], fl), t
    assert n == 4          # the four settled GASD contracts have both a row and a tape here


def _pin_replay_matches_venue_realized_pnl():
    for t, v in VENUE_POSITIONS.items():
        pos, _avg, real, _fee = SP.replay(BY_TICKER[t])
        assert real == pytest.approx(v["realized_pnl_dollars"], abs=CENT), t
        assert pos == pytest.approx(v["position_fp"], abs=CENT), t


def _pin_flat_book_synthetic():
    """Bought 10 YES at 0.40 and sold 10 at 0.45 (booked as `sell no @ 0.55`). Flat, result no."""
    row = _mk("X-1-1", "no", 10, 10, 4.00, 5.50, 0)
    fills = [_fill("X-1-1", 1, "buy", 10, 0.40), _fill("X-1-1", 2, "sell", 10, 0.45)]
    assert SP.matched_pairs(row) == 10
    assert SP.settlement_row_pnl(row) == pytest.approx(0.50, abs=1e-9)
    assert SP.tape_pnl(fills, "no") == pytest.approx(0.50, abs=1e-9)


def _pin_zero_crossing_synthetic():
    """Bought 5 YES at 0.30 then sold 10 at 0.35 - flips to 5 NO. Result no, so the NO pays $1."""
    row = _mk("X-1-2", "no", 5, 10, 1.50, 6.50, 500)
    fills = [_fill("X-1-2", 1, "buy", 5, 0.30), _fill("X-1-2", 2, "sell", 10, 0.35)]
    assert SP.expected_revenue_dollars(row) == pytest.approx(5.0)
    assert SP.settlement_row_pnl(row) == pytest.approx(2.00, abs=1e-9)
    assert SP.tape_pnl(fills, "no") == pytest.approx(2.00, abs=1e-9)


def _pin_csv_agreement_before_cutoff():
    m, comp, diffs, _excl = SP.crossvalidate_csv(SETTLEMENT_ROWS)
    assert comp == 47
    assert diffs == []
    assert m == 47


def _pin_csv_excludes_exactly_the_post_cutoff_event():
    _m, _c, _d, excl = SP.crossvalidate_csv(SETTLEMENT_ROWS)
    assert sorted(excl) == sorted(GASD_TICKERS)
    for t in excl:
        assert ROW[t]["settled_time"][:19] > SP.CSV_EXPORT_CUTOFF_UTC


def _pin_csv_file_is_the_expected_receipt():
    """Guards the evidence file itself: canon M8 publishes -79.9931 over its `trade` rows."""
    tot = SP.load_csv_realized()
    assert len(tot) == 54
    assert sum(tot.values()) == pytest.approx(-79.9931, abs=0.01)


def _pin_event_grouping_follows_canon_T():
    assert SP.event_of("KXAAAGASD-26JUL23-4.100") == "KXAAAGASD-26JUL23"
    assert SP.series_of("KXAAAGASD-26JUL23-4.100") == "KXAAAGASD"
    assert SP.event_of("KXTEMPCHIH-26JUL2212-T69.99") == "KXTEMPCHIH-26JUL2212"


def _pin_per_event_totals():
    contracts = SP.per_contract(SETTLEMENT_ROWS, BY_TICKER)
    events = SP.per_event(contracts)
    assert sum(v["contracts"] for v in events.values()) == 51
    assert events[GASD_EVENT]["contracts"] == 4
    assert events[GASD_EVENT]["pnl"] == pytest.approx(-7.4656, abs=0.01)
    assert sum(v["pnl"] for v in events.values()) == pytest.approx(-87.5946, abs=0.02)


def _pin_fee_cost_is_lifetime_trading_fees():
    """Not a settlement charge (canon M9: "There is no settlement fee") - so it belongs in the
    lifetime P&L exactly once, which is what settlement_row_pnl does."""
    total_fee = sum(SP._f(r["fee_cost"]) for r in SETTLEMENT_ROWS)
    assert total_fee == pytest.approx(2.5158, abs=0.01)
    r = ROW["KXAAAGASD-26JUL21-4.020"]
    assert SP._f(r["fee_cost"]) == pytest.approx(0.2218, abs=1e-6)
    # fees are inside the lifetime number, once
    assert SP.settlement_row_pnl(r) == pytest.approx(
        SP.revenue_dollars(r) + SP.matched_pairs(r)
        - SP._f(r["yes_total_cost_dollars"]) - SP._f(r["no_total_cost_dollars"]) - 0.2218,
        abs=1e-9)


PNL_PINS = [
    _pin_naive_reproduces_the_impossible_442,
    _pin_correct_model_is_physically_possible,
    _pin_gasd_event_lifetime_pnl,
    _pin_leg_plus_realized_equals_lifetime,
    _pin_two_models_agree_on_every_taped_contract,
    _pin_flat_book_synthetic,
    _pin_zero_crossing_synthetic,
    _pin_csv_agreement_before_cutoff,
    _pin_per_event_totals,
]

TAPE_PINS = [
    _pin_gasd_settlement_leg_and_basis,
    _pin_leg_plus_realized_equals_lifetime,
    _pin_two_models_agree_on_every_taped_contract,
    _pin_replay_matches_venue_realized_pnl,
    _pin_flat_book_synthetic,
    _pin_zero_crossing_synthetic,
]

REVENUE_PINS = [
    _pin_documented_row_reconciles,
    _pin_revenue_zero_when_net_is_on_the_losing_side,
    _pin_revenue_reconciles_on_every_row,
    _pin_zero_crossing_synthetic,
    _pin_counts_are_cumulative_not_net,
    _pin_net_position_is_yes_signed,
]


def _count_failures(pins):
    failed = 0
    for p in pins:
        try:
            p()
        except AssertionError:
            failed += 1
    return failed


# ============================================================================================
# tests
# ============================================================================================
def test_revenue_is_cents_not_dollars():
    _pin_revenue_is_cents()


def test_counts_are_cumulative_not_net():
    _pin_counts_are_cumulative_not_net()


def test_net_position_is_yes_signed():
    _pin_net_position_is_yes_signed()


def test_documented_row_reconciles():
    _pin_documented_row_reconciles()


def test_revenue_zero_when_net_is_on_the_losing_side():
    _pin_revenue_zero_when_net_is_on_the_losing_side()


def test_revenue_reconciles_on_every_settlement_row():
    _pin_revenue_reconciles_on_every_row()


def test_naive_reading_reproduces_the_impossible_442():
    _pin_naive_reproduces_the_impossible_442()


def test_correct_model_is_physically_possible():
    _pin_correct_model_is_physically_possible()


def test_gasd_26jul23_event_lifetime_pnl():
    _pin_gasd_event_lifetime_pnl()


def test_gasd_26jul23_settlement_leg_reproduces_minus_8_20():
    _pin_gasd_settlement_leg_and_basis()


def test_leg_plus_realized_equals_lifetime():
    _pin_leg_plus_realized_equals_lifetime()


def test_two_models_agree_on_every_taped_contract():
    _pin_two_models_agree_on_every_taped_contract()


def test_replay_matches_venue_realized_pnl():
    _pin_replay_matches_venue_realized_pnl()


def test_flat_book_synthetic():
    _pin_flat_book_synthetic()


def test_zero_crossing_synthetic():
    _pin_zero_crossing_synthetic()


def test_csv_agreement_before_cutoff():
    _pin_csv_agreement_before_cutoff()


def test_csv_excludes_exactly_the_post_cutoff_event():
    _pin_csv_excludes_exactly_the_post_cutoff_event()


def test_csv_file_is_the_expected_receipt():
    _pin_csv_file_is_the_expected_receipt()


def test_event_grouping_follows_canon_T():
    _pin_event_grouping_follows_canon_T()


def test_per_event_totals():
    _pin_per_event_totals()


def test_fee_cost_is_lifetime_trading_fees():
    _pin_fee_cost_is_lifetime_trading_fees()


# --------------------------------------------------------------------------------------------
# META-TESTS - these MEASURE the pin, they do not assert it in prose.
# --------------------------------------------------------------------------------------------
def test_pnl_pins_fail_under_the_prefix_naive_formula(monkeypatch):
    """Swap settlement_row_pnl for the -$442 formula and count how many pins break."""
    assert _count_failures(PNL_PINS) == 0
    monkeypatch.setattr(SP, "settlement_row_pnl", SP.naive_row_pnl)
    assert _count_failures(PNL_PINS) == 8      # all but the naive pin itself, which asserts it


def test_tape_pins_fail_if_sell_no_is_read_as_adding_yes(monkeypatch):
    """The other plausible misreading: treating `sell no` as opening a LONG yes instead of
    reducing it. The CSV receipt refutes it; this measures that the pins catch it."""
    assert _count_failures(TAPE_PINS) == 0
    monkeypatch.setattr(SP, "fill_signed_qty",
                        lambda f: abs(SP._f(f.get("count_fp") or f.get("count"))))
    assert _count_failures(TAPE_PINS) == 6


def test_revenue_pins_fail_if_counts_are_read_as_net(monkeypatch):
    """Read yes_count_fp/no_count_fp as if they were already net (the root misreading)."""
    assert _count_failures(REVENUE_PINS) == 0
    monkeypatch.setattr(SP, "net_position", lambda r: SP._f(r.get("yes_count_fp")))
    assert _count_failures(REVENUE_PINS) == 5


# ---- SIGN CONVENTION REGRESSION (added 2026-07-25 after a real analysis bug) ----
# An analysis script inverted (action='sell', side='no') to +1 signed-YES and
# produced a false conclusion. The venue labels our resting NO bid's fill as
# "sell no" (our client submits NO bids as ASKS on the yes-scale) even though we
# are ACQUIRING NO = SHORT yes. Correct sign is ACTION-ONLY. Validated against
# /portfolio/positions (21/21 tickers reconciled). This pins it forever.

def test_fill_signed_qty_is_action_only_all_four_cases():
    import kalshi_settlement_pnl as S

    def venue_validated(action, side):
        if side == "yes":
            return +1.0 if action == "buy" else -1.0
        return -1.0 if action == "sell" else +1.0

    for action in ("buy", "sell"):
        for side in ("yes", "no"):
            got = S.fill_signed_qty({"action": action, "side": side,
                                     "count_fp": "1"})
            assert got == venue_validated(action, side), (action, side, got)


def test_sell_no_is_negative_yes_signed():
    """The exact case that was inverted. ('buy','yes') and ('sell','no') are the
    only two shapes this bot produces, so getting this wrong corrupts ~half of
    every fill-derived number."""
    import kalshi_settlement_pnl as S
    assert S.fill_signed_qty({"action": "sell", "side": "no",
                              "count_fp": "20"}) == -20.0
    assert S.fill_signed_qty({"action": "buy", "side": "yes",
                              "count_fp": "20"}) == +20.0
