# 家庭空间隐患演示（risk-v4）

独立 `--profile risk`，默认仍为 `rich`，`lived` 不变。未修改真实扫描、SpatialLM、尺寸或风险规则。70/30 是布局设计意图，不是测量得到的风险面积比例。

## 复现

在项目根目录使用现有后端 Python：

```text
python backend/scripts/house_demo.py generate --profile risk --output <新的空目录>
python backend/scripts/house_demo.py infer --output <该目录> --rooms living_room bedroom kitchen bathroom corridor
python backend/scripts/house_demo.py serve --output <该目录> --port 8095
```

生成目录：`backend/data/demos/house_demo_risk_20261013`，202306 个整屋去重点，五个房间。原版数据未覆盖。

## 增量布局

- 客厅茶几靠近沙发，小桌与绿植靠近入口路线；桌面物体随桌子刚体移动。
- 卧室椅子拉出并偏转，原床边近距离保留。
- 厨房餐桌靠近操作区和冰箱，椅子偏移。
- 卫生间洗衣机与储物柜进入局部活动区。
- 走廊鞋柜突出，新增绿植、装饰储物柜；八个主要对象。

## 生成几何证据（非模型识别数值）

| 房间 | 原 lived 路线最小净空 m | risk 路线最小净空 m |
|---|---:|---:|
| 客厅 | 0.900 | 0.640 |
| 卧室 | 0.900 | 0.537 |
| 厨房 | 0.792 | 0.640 |
| 卫生间 | 0.560 | 0.253 |
| 走廊 | 0.640 | 0.400 |

客厅沙发与茶几平面间距约 0.08 m。卫生间 0.253 m 是明显狭窄的局部隐患示例，不代表人能正常通行。门洞本身仍为 0.9 × 2.1 m，不等于门内路线全程宽度。

`ground_truth.json` 保留类型、世界坐标、尺寸、朝向和点索引。
`layout_analysis.json` 保留家具间距、路线净空、门内区域占用和可追溯规则建议。
`predictions.json` 是真实 SpatialLM 输出；`model_layout_analysis.json` 仅基于模型物体，不用生成真值补漏。

路线为通向固定活动点的单条栅格路线估计，不是全局最宽通路；不模拟人体、门扇、冰箱开门或湿滑。没有命中建议不代表安全。无正式安全评分，也不声称新增跌倒风险检测模型。

## 验证

61 项相关 pytest 通过；preview chunks 通过。测试覆盖各房间点云边界、6–8 个主要物体、无非预期穿插、门洞完整、桌面支撑、几何路线及相对原版净空收窄。五房间浏览器 WebGL、标签、证据来源切换及显示开关检查通过。

真实 SpatialLM 顺序推理五房间均 done：客厅 3、卧室 5、厨房 5、卫生间 4、走廊 3，共 20 个模型对象。存在漏检/类别误识别，不能等同于生成场景中的全部家具；查看完整设计布局应选择“生成真值”，核查模型能力应选择“SpatialLM 模型结果”。
