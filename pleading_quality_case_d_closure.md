# Case D — One Contract / Multiple EvidenceItems Closure

Generated: 2026-09-10T13:37:02.406640+00:00
LLM_MODE: deterministic

## Counts

| Metric | Value |
|--------|-------|
| CaseMaterial (contract) | 1 |
| EvidenceItem (contract) | 4 |
| SourceSpan (contract) | 4 |
| evidence_directory total rows | 5 |
| evidence_directory contract rows | 1 |
| merged_evidence_refs | 4 |

## Readiness / Validator

- Readiness: `READY` 
- Validator passed: `True`
- repair_count: `0`

## Evidence Directory (contract)

```
证据目录

1. 《设计优化咨询服务合同》
   证明目的：一、服务范围：设计优化咨询及成果提交；三、付款条件：成果提交并签收后15日内支付；二、收费标准：按经确认优化金额的8%计取，上限30万元；合同载明：甲方：中梁地产集团有限公司
乙方：四川维海科技有限公司
   来源：原件/扫描件

2. 《supplement-d6fb18》
   证明目的：2024年6月20日，原告向被告提交优化成果，被告签收确认
   来源：原件/扫描件

3. 《supplement-37045b》
   证明目的：经确认优化金额为200万元，按8%计算应付服务费160000元
   来源：原件/扫描件

4. 《supplement-a288df》
   证明目的：被告已支付60000元，尚欠100000元未付
   来源：原件/扫描件

5. 《supplement-ac7f33》
   证明目的：付款条件已成就，债务已到期
   来源：原件/扫描件
```

## Provenance Preservation

```json
{
  "material_id": "7acb04e8-7d68-4bfa-971a-cf27d6a426ad",
  "items": [
    {
      "evidence_item_id": "13ae2b0b-50a3-419f-bec2-34f9600811e2",
      "source_span_ids": [
        "5f2a1ae4-a65d-4013-8dd8-07aa345ae550"
      ],
      "material_ids": [
        "7acb04e8-7d68-4bfa-971a-cf27d6a426ad"
      ],
      "quotes": [
        "甲方：中梁地产集团有限公司\n乙方：四川维海科技有限公司\n"
      ]
    },
    {
      "evidence_item_id": "2affaaa6-3db3-45ca-91f6-eecaf9aecb69",
      "source_span_ids": [
        "e72c5857-ad5d-4e6f-989e-1373f730267c"
      ],
      "material_ids": [
        "7acb04e8-7d68-4bfa-971a-cf27d6a426ad"
      ],
      "quotes": [
        "一、服务范围：设计优化咨询及成果提交。\n"
      ]
    },
    {
      "evidence_item_id": "f203d248-bb9c-437a-b5d8-b08a32f00678",
      "source_span_ids": [
        "6316c56a-1139-4c25-a045-47d51e6f258b"
      ],
      "material_ids": [
        "7acb04e8-7d68-4bfa-971a-cf27d6a426ad"
      ],
      "quotes": [
        "二、收费标准：按经确认优化金额的8%计取，上限30万元。\n"
      ]
    },
    {
      "evidence_item_id": "2b7c6be6-795e-4d90-becb-bfe149e4d3dd",
      "source_span_ids": [
        "4e4e16ae-c2fa-411a-af8b-b7fda0c1c75b"
      ],
      "material_ids": [
        "7acb04e8-7d68-4bfa-971a-cf27d6a426ad"
      ],
      "quotes": [
        "三、付款条件：成果提交并签收后15日内支付。\n"
      ]
    }
  ],
  "material_filename": "设计优化咨询服务合同.pdf"
}
```

## merged_evidence_refs

```json
[
  {
    "evidence_item_id": "13ae2b0b-50a3-419f-bec2-34f9600811e2",
    "evidence_item_version": 1
  },
  {
    "evidence_item_id": "2affaaa6-3db3-45ca-91f6-eecaf9aecb69",
    "evidence_item_version": 1
  },
  {
    "evidence_item_id": "f203d248-bb9c-437a-b5d8-b08a32f00678",
    "evidence_item_version": 1
  },
  {
    "evidence_item_id": "2b7c6be6-795e-4d90-becb-bfe149e4d3dd",
    "evidence_item_version": 1
  }
]
```

## Full Text

```
民事起诉状

原告：四川维海科技有限公司
住所地：【待补充】
法定代表人：【待补充】
统一社会信用代码：【待补充】
联系方式：【待补充】

被告：中梁地产集团有限公司
住所地：【待补充】
法定代表人：【待补充】
统一社会信用代码：【待补充】
联系方式：【待补充】

诉讼请求：
一、判令被告向原告支付剩余服务费人民币100,000元。
二、本案诉讼费用由被告承担。

事实与理由：
合同载明：甲方：中梁地产集团有限公司
乙方：四川维海科技有限公司
合同载明：一、服务范围：设计优化咨询及成果提交。
合同载明：二、收费标准：按经确认优化金额的8%计取，上限30万元。
合同载明：三、付款条件：成果提交并签收后15日内支付。
2024年6月20日，原告向被告提交优化成果，被告签收确认。
付款条件已成就，债务已到期。
经确认优化金额为200万元，按8%计算应付服务费160000元。 被告已支付60000元，尚欠100000元未付。

此致
【待确认有管辖权的人民法院】

具状人：四川维海科技有限公司
日期：【待律师确认】

```
