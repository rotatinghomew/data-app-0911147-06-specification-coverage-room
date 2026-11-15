# 含检出边界资料的总体规格覆盖与容忍声明

这是供本地讨论使用的数据应用。当前版本提供逐属性描述统计、检测限展示、既有规格逐条比对，以及面向总体覆盖的容忍声明分析：正向按覆盖比例与置信程度评估可支持的声明，反向按给定规格评估覆盖证据；逐项、多项同时与多维联合分别给出。检测限记录不作为实际读数，样本或证据不足时明说无法支持，不把原始描述或假设敏感的参考区间当作已经验证的推断结论。

## 环境与启动

使用 Python 3.11.14，数值依赖锁定在 requirements.txt。Docker 基础镜像按内容摘要固定；构建阶段安装依赖，服务、检查和演示阶段不下载数据、依赖或模型。所有业务记录都是本地构造的课程与研究管理资料，不包含个人或真实实验信息。

```sh
docker build -t specification-coverage-room:local .
docker run --rm -p 8080:8080 specification-coverage-room:local
```

浏览器打开本机 8080 端口。复用已有 Python 环境时，先在准备阶段安装锁定依赖，再运行服务：

```sh
python3 -m pip install --only-binary=:all: -r requirements.txt
python3 -m app.service
```

浏览器回归使用锁定的 Playwright 1.56.0 与其配套 Chromium。镜像构建阶段预装浏览器和系统库，测试与服务运行不下载任何依赖，不依赖宿主机的 Chrome 或 Edge。直接使用 Python 环境时，也需在准备阶段执行 `python3 -m playwright install --with-deps chromium`。

APP_STUDY 可以指定同结构本地资料，APP_STATE_DIR 指定批注目录，默认是 .local。批注只保存人工文字与当时的表单选择，不冻结分析数据或生成统计发布；删除该目录可回到干净本地状态，原始资料不变。

## 构建与检查

```sh
python3 -m compileall -q app tests
python3 -m unittest discover -s tests -v
docker run --rm --network none specification-coverage-room:local python -m unittest discover -s tests -v
```

测试覆盖现行领域语义、容忍声明数值（含 analysis-cases.json 独立检查）、HTTP 错误边界和批注重开，并在真实 Chromium 中遍历资料表、核对表单响应与实时 HTTP 结果一致、保存批注并刷新确认。浏览器缺失或脚本报错会令测试失败，不跳过检查。data/analysis-cases.json 是后续分析可使用的独立检查资料，docs/analysis-contract.md 说明其统计意义和适用限制；这些资料不要求每次输出某种结论方向。新增分析需要自行补上相应数值、业务与页面回归验证。

## 短时间实机查看

```sh
python3 -m app.demo
```

该入口启动真实本地 HTTP 服务，从健康检查到当前业务操作（含容忍声明）逐项打印完整响应，随后关闭服务。默认资料在九十秒内完成，不读取预存日志。网页交互也使用同一服务。当前入口仅展示现有产品；后续新增的分析应通过真实接口进入该流程并保持短时可运行，不伪造预期成功。正式运行前先完成镜像构建和依赖准备。

## 代码位置

app/domain.py 定义业务资料语义与全部操作（summary 描述与 tolerance 容忍声明）；app/service.py 是网页和接口共同使用的服务；web/app.js 提交表单并呈现原始记录与声明结果；app/storage.py 保存工作批注。外部接口与分析边界分别见 docs/http-contract.md 和 docs/analysis-contract.md。
