# MySQL 单表数据同步项目骨架

用于从 MySQL 8.0 源库 `SAP_RW_Material_Move` 同步数据到目标库 `record_frozen_data`。业务上将 `ID`（形如 `5003792664+2026+0012`）作为增量游标；所提供的源表 DDL 将它定义为可空 `varchar(50)`，没有声明 `PRIMARY KEY` 或 `UNIQUE` 约束，因此程序每次执行会检查 ID 非空且唯一。首次同步从头读取，此后使用目标库内部状态表 `_mysql_table_sync_state` 保存已提交的最后 ID。

## 项目结构

```text
.
├── .env.example
├── pyproject.toml
├── src/mysql_table_sync/
│   ├── cli.py          # 命令行入口
│   ├── config.py       # 环境变量配置及校验
│   ├── database.py     # SQLAlchemy Engine 生命周期
│   ├── exceptions.py  # 应用异常
│   └── service.py      # 同步服务扩展点
└── tests/
```

## 环境准备

项目要求 Python 3.11 或更高版本。安装项目及测试依赖：

```powershell
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`，填写源库和目标库各自的连接 URL。MySQL 驱动格式示例：

```text
mysql+pymysql://用户名:密码@主机:3306/数据库名?charset=utf8mb4
```

请对密码中的特殊字符进行 URL 编码，并确保连接账户只具有同步所需的最小权限。不要将真实 `.env` 提交到版本库。

日志配置可在 `.env` 中调整：`LOG_FILE` 默认是 `logs/mysql_table_sync.log`，`LOG_MAX_BYTES` 默认 10 MiB，`LOG_BACKUP_COUNT` 默认保留 5 个轮转文件。日志同时写入控制台和 UTF-8 文件；记录运行开始/结束、每批读取和写入汇总数、已提交游标及错误，不记录连接 URL、密码或源数据行。相对日志路径以项目工作目录为基准。

`SOURCE_EXCLUDED_IDS` 为逗号分隔的源记录 ID 排除列表，默认值是 `OnlyKey`；若确认还存在其他异常 ID，可在 `.env` 中填写，例如 `SOURCE_EXCLUDED_IDS=OnlyKey,BAD_ROW`。空值表示不排除任何 ID。

## 已确认的源数据筛选规则

- MovementType 分类由 `MaterialType` 决定，不使用对所有物料通用的 movement type 集合。采购编码追加 `_P`，含水编码追加 `_MC`，消耗使用基础编码。
- 默认排除 `ID=OnlyKey`，并可通过 `SOURCE_EXCLUDED_IDS` 配置其他排除项。
- 按 `MaterialType` 取得类型短码，再直接拼接源字段 `Material` 生成基础 `meter_number`（不加分隔符）。例如硅铁、`Material=20056684` 得到 `SFE20056684`。采购在基础编码后追加 `_P`，得到 `SFE20056684_P`；含水量追加 `_MC`，得到 `SFE20056684_MC`。`MovementType=102` 会同时产生 `_P` 和 `_MC` 两类记录。

| MaterialType | 类型短码 |
| --- | --- |
| 硅铁 | `SFE` |
| 柴油 | `DIE` |
| 焦炭 | `COK` |
| 煤炭 | `COA` |
| 白云石 | `DOL` |
| 废钢 | `SCR` |
| 兰炭 | `SEM` |
| 石灰石 | `LIM` |

| MaterialType | 采购量 MovementType | 消耗量 MovementType | 含水量 MovementType |
| --- | --- | --- | --- |
| 煤炭 | `101, 102` | `291, 292` | `102` |
| 兰炭 | `101, 102` | `291, 292` | `102` |
| 焦炭 | `101, 102` | `291, 292` | `102` |
| 硅铁 | `105` | `201, 202, 291, 292` | 不处理 |
| 石灰石 | `101, 102` | `291, 292` | `102` |
| 白云石 | `101, 102` | `291, 292` | `102` |
| 柴油 | `101` | `201` | 不处理 |
| 废钢 | `101, 102, 105` | `201, 202, 291, 292` | 不处理 |

对煤炭、兰炭、焦炭、石灰石、白云石，`MovementType=102` 同一源记录同时生成采购 `_P` 和含水 `_MC` 两个目标分类；废钢的 `102` 仅作采购。硅铁、柴油的 `102` 不在对应规则中，会被跳过。

未映射的物料类型、空 `Material` 或不在对应矩阵中的 MovementType 会被跳过。每条有效源记录除原有 Material 明细汇总外，还会计入该 `MaterialType` 的每日分类总汇总：用量编码为 `<类型短码>_ALL`，采购为 `<类型短码>_ALL_P`，含水量为 `<类型短码>_ALL_MC`。例如煤炭会分别生成 `COA_ALL`、`COA_ALL_P` 和 `COA_ALL_MC`。汇总键为 `(meter_number, PostingDate)`；明细行仍按 Material 区分，ALL 行跨同一 MaterialType 下不同 Material 合计，但不会把用量、采购、含水类别混在一起。规则函数位于 `src/mysql_table_sync/mapping.py`。

## 命令

```powershell
mysql-table-sync --help
mysql-table-sync check
mysql-table-sync sync
```

`check` 会分别连接两个库执行 `SELECT 1`。`sync` 首次全量读取，随后按 `ID` 增量；每批目标数据写入与游标推进处于同一事务。`SOURCE_EXCLUDED_IDS` 中的异常 ID 会在源查询阶段就被过滤掉，不会进入游标：由于 `ID` 是字符串，`OnlyKey` 这类以字母开头的值在字符序上大于所有以数字开头的业务 ID，若让它成为游标，后续 `ID > 'OnlyKey'` 将永远查不到新数据。这些 ID 同样不参与启动时的唯一性校验——源表中这类脏数据常常重复出现，而它们本身不会被同步。相同日汇总键的新数据会累加到目标记录，目标已有多个相同键时会报错停止。可在仓库根目录运行 `python -m mysql_table_sync.cli --help`，或执行 `pytest`。

## 每日定时执行（Windows）

项目包含任务计划程序注册脚本。先在 PowerShell 中切到项目根目录，确认 `.env` 和 `.venv` 已配置，然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Register-DailySyncTask.ps1
```

默认每天本地时间 02:00 执行。可指定任务名称和时间，例如：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Register-DailySyncTask.ps1 -TaskName "Material-Daily-Sync" -StartTime "01:30"
```

任务以注册时的当前 Windows 用户交互式运行，不会把账户密码保存进脚本；该用户必须能读取 `.env`、运行项目虚拟环境，并访问源/目标数据库。若机器在计划时间关机，`StartWhenAvailable` 会在之后可运行时补执行；如果用户未登录，任务会等该用户下次登录。查看或手动触发任务：

```powershell
Get-ScheduledTask -TaskName "MySQL-Material-Sync"
Start-ScheduledTask -TaskName "MySQL-Material-Sync"
```

同步日志默认在 `logs/mysql_table_sync.log`，文件达到配置大小后轮转为 `.log.1` 等备份文件。删除每日任务：

```powershell
Unregister-ScheduledTask -TaskName "MySQL-Material-Sync" -Confirm:$false
```

## Linux 生产部署（systemd）

Linux 生产使用 `systemd` oneshot service + daily timer。由于客户服务器不能访问外网且只有 Python 3.8，本方案在联网的 GitHub Actions runner 上把 Python 运行时及项目依赖打包成 Linux 单文件二进制；客户服务器不需要安装 Python、pip、venv 或 Python 依赖。二进制构建 workflow 为 `.github/workflows/build-linux-binary.yml`，systemd 模板位于 `deploy/systemd/`。目标是 Linux x86_64、glibc 2.31 基线（Ubuntu 20.04）。

### 1. 在联网环境构建二进制

将当前项目提交并推送到有 GitHub Actions 权限的仓库，然后在 GitHub 网页打开 **Actions → Build Linux standalone binary → Run workflow**。workflow 先运行 `pytest`，通过后在 Debian Bullseye（glibc 2.31）容器中用 PyInstaller 构建，产出名为 `mysql-table-sync-linux-x86_64-glibc-2.31` 的 artifact。

GitHub 下载的 artifact 是 zip，解压后得到：

```text
mysql-table-sync-linux-x86_64-glibc-2.31.tar.gz
mysql-table-sync-linux-x86_64-glibc-2.31.tar.gz.sha256
```

先校验再解包：

```bash
sha256sum -c mysql-table-sync-linux-x86_64-glibc-2.31.tar.gz.sha256
tar xzf mysql-table-sync-linux-x86_64-glibc-2.31.tar.gz
```

解包后的目录内容：

```text
mysql-table-sync-linux-x86_64-glibc-2.31/
├── mysql-table-sync                    # 单文件二进制（已含 Python 运行时与依赖）
├── mysql-table-sync.service
├── mysql-table-sync-check.service
├── mysql-table-sync.timer
└── mysql-table-sync.env.example        # 仅变量模板，不含真实凭据
```

不要使用 Windows `.exe`，也不要把 Windows `.venv` 复制到 Linux。若项目不能推送到 GitHub，可在一台可联网的 x86_64 Debian 11/Ubuntu 20.04 构建机按 workflow 中的 PyInstaller 命令构建。

### 2. 复制文件到客户服务器

服务器只需这些文件：

- 构建产物 `mysql-table-sync`。
- `mysql-table-sync.service`。
- `mysql-table-sync-check.service`。
- `mysql-table-sync.timer`。
- 单独配置的 `/etc/mysql-table-sync.env`（不要从仓库复制 `.env` 或提交凭据）。

例如从运维工作站复制二进制和 unit 文件：

```bash
cd mysql-table-sync-linux-x86_64-glibc-2.31
scp mysql-table-sync dba@SERVER:/tmp/
scp mysql-table-sync.service dba@SERVER:/tmp/
scp mysql-table-sync-check.service dba@SERVER:/tmp/
scp mysql-table-sync.timer dba@SERVER:/tmp/
```

### 3. 安装二进制和检查系统兼容性

先在客户服务器确认系统基线满足 glibc 2.31：

```bash
cat /etc/os-release
ldd --version | head -n 1
```

`ldd` 版本需为 2.31 或更高（Ubuntu 20.04 / Debian 11 及以上满足）。再确认 `dba` 用户/组及 systemd 存在，然后安装文件和日志目录：

```bash
getent passwd dba
getent group dba
systemctl --version
sudo install -d -o root -g dba -m 0750 /opt/sgb-matsync
sudo install -d -o dba -g dba -m 0750 /opt/sgb-matsync/logs
sudo install -o root -g dba -m 0750 /tmp/mysql-table-sync /opt/sgb-matsync/mysql-table-sync
```

验证二进制架构及系统动态库：

```bash
file /opt/sgb-matsync/mysql-table-sync
ldd /opt/sgb-matsync/mysql-table-sync
```

`file` 应显示 x86-64 Linux ELF；`ldd` 不应有 `not found`。目标系统 glibc 应为 2.31 或更高。该二进制已包含 Python 运行时和 Python 依赖，但仍使用 Linux 系统的 glibc 等基础共享库。若服务器不是 x86_64 或 glibc 低于 2.31，需要按客户系统版本重新构建，不能用 Windows `.exe` 替代。

确认二进制可执行：

```bash
sudo -u dba /opt/sgb-matsync/mysql-table-sync --help
```

### 4. 配置生产连接

将生产变量写入 `/etc/mysql-table-sync.env`，不要把生产凭据放进代码仓库：

```ini
SOURCE_DATABASE_URL=mysql+pymysql://user:encoded_password@source-host:3306/source_db?charset=utf8mb4
TARGET_DATABASE_URL=mysql+pymysql://user:encoded_password@target-host:3306/target_db?charset=utf8mb4
SOURCE_TABLE=SAP_RW_Material_Move
TARGET_TABLE=record_frozen_data
SOURCE_PRIMARY_KEY=ID
SOURCE_EXCLUDED_IDS=OnlyKey
BATCH_SIZE=1000
LOG_LEVEL=INFO
LOG_FILE=/opt/sgb-matsync/logs/mysql_table_sync.log
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

把 `source_db`、`target_db`、用户和主机替换成生产值；密码中的 URL 特殊字符需百分号编码。`LOG_FILE` 必须写绝对路径，否则日志会落到服务的工作目录下。限制配置文件权限：

```bash
sudo chown root:dba /etc/mysql-table-sync.env
sudo chmod 0640 /etc/mysql-table-sync.env
```

目标库账号除读写 `record_frozen_data` 外，还需要目标库的 `CREATE` 权限：首次运行会自动创建游标状态表 `_mysql_table_sync_state`。

手动执行二进制时 systemd 不会注入 `EnvironmentFile`，需要先导入环境变量：

```bash
sudo -u dba bash -c 'set -a; . /etc/mysql-table-sync.env; set +a; /opt/sgb-matsync/mysql-table-sync check'
```

### 5. 安装并先做连通性检查

```bash
sudo install -o root -g root -m 0644 mysql-table-sync.service /etc/systemd/system/
sudo install -o root -g root -m 0644 mysql-table-sync-check.service /etc/systemd/system/
sudo install -o root -g root -m 0644 mysql-table-sync.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/mysql-table-sync.service /etc/systemd/system/mysql-table-sync-check.service /etc/systemd/system/mysql-table-sync.timer
sudo systemctl daemon-reload
sudo systemctl start mysql-table-sync-check.service
sudo systemctl status mysql-table-sync-check.service --no-pager
```

连通性检查只执行 `SELECT 1`，不读写业务表。确认生产源/目标库身份、账号权限、目标表与游标状态正确后，再手动执行一次同步作为试运行：

```bash
sudo systemctl start mysql-table-sync.service
sudo systemctl status mysql-table-sync.service --no-pager
sudo journalctl -u mysql-table-sync.service -n 100 --no-pager
sudo tail -n 100 /opt/sgb-matsync/logs/mysql_table_sync.log
```

### 6. 启用每日任务

人工核对试运行结果后启用每天执行：

```bash
sudo systemctl enable --now mysql-table-sync.timer
systemctl list-timers mysql-table-sync.timer --all
```

`Persistent=true` 会在服务器关机错过计划时间后，于系统恢复时补执行一次；`RandomizedDelaySec=5m` 用于错开整点负载。定时任务失败时，systemd 状态、journal 和应用轮转日志均可用于排查。停止/禁用任务：

```bash
sudo systemctl disable --now mysql-table-sync.timer
```

**历史数据注意：**现有 ID 游标只同步游标之后的新源记录。部署新版本不会自动重算旧 ID，也不会自动补出新增加的 `<类型短码>_ALL` 历史汇总。生产首次启用或做历史回补前，应先核对目标表和 `_mysql_table_sync_state`，制定备份、目标数据迁移/清理及游标重置方案；不要在已有数据上盲目删除游标后重跑，否则服务会把已有同键数量/金额再次累加。

增量正确性要求源 `ID` 的数据库排序顺序与新数据产生顺序一致，且 ID 已写入后不再修改。由于 `ID` 是字符串且 DDL 未声明唯一约束，格式示例本身不足以证明这一点；如果一条新业务记录后来获得的 ID 小于已保存游标，它会被跳过。建议在源库确认 ID 生成顺序并建立 `UNIQUE NOT NULL` 约束。状态表只记录最后已提交 ID，不保存字段映射版本；运行期间不要更改聚合口径后直接沿用旧游标。

## 实现同步前需确认

已提供的源表字段为 `Material`、`MaterialType`、`MovementType`、`Qty`、`Unit`、`CNY`、`PostingDate`、`CC`、`CO`、`DocumentHeader`、`DocDate` 和 `ID`；DDL 中这些字段均为可空的 `varchar(50)`。

目标表 `record_frozen_data` 的字段为 `id`（自增主键）、`frozen_time`、`meter_number`、`dvalue`、`price`、`last_value`、`current_value`、`last_time`、`current_time`、`update_at` 和 `stat_type`。目标表没有源 `ID` 字段，`(meter_number, frozen_time, stat_type)` 也没有唯一约束。当前实现用这三列匹配日汇总并更新数量/金额；若查询到多个匹配项会停止。建议确认该复合键业务唯一，并在目标库加唯一约束，以从数据库层保证并发和重试安全。

已实现的聚合口径：仅处理上方逐 MaterialType 配置的采购/消耗/含水 movement type，并排除异常 ID。同一源行会分别进入 Material 明细组和对应 MaterialType 的 `_ALL` 总组；如矩阵将该 MovementType 同时标记为采购和含水，则两种类别各自都有明细组及 ALL 组。随后按 `(meter_number, PostingDate)` 分组，`PostingDate` 按 `YYYYMMDD` 解析为目标日。`Qty` 转换为吨后求和，`CNY` 同组求和并作为聚合结果的 `price`。目标行构造时，`last_value`、`current_value`、`last_time`、`current_time` 不提供，`update_at` 使用写入时刻，`stat_type` 固定为 `1`。仍需确认 `CNY` 字段确为每行金额而非单价。

目标 `dvalue` 统一以吨 (`t`) 存储：硅铁、焦炭、煤炭、白云石、废钢和石灰石的 `kg` 数量除以 1000；柴油的 `l` 数量乘以密度 `0.831 kg/l` 后除以 1000；兰炭的 `t`/`TO` 数量保持不变，`kg` 除以 1000。源单位按大小写不敏感处理。Qty/CNY 支持 `-10,709.36` 这种三位千分位逗号格式，非法分组仍会报错。其他不符合物料单位规则的行会报错，不会静默按错误单位合并。聚合结果使用 Decimal 精确计算；正负数量和金额保留符号参与净额计算。

注意：开发库首次同步的目标记录是在此吨换算规则加入前写入的旧口径数据。程序升级后，ID 游标已到源表最大值，普通增量运行不会回溯重算这些行；需要单独制定并授权历史数据迁移或清空目标后重跑方案，避免新旧单位混存。

MovementType 分类变化也不会自动重放游标之前的源行。若需按上述物料矩阵重新分类历史数据，需先清理/迁移对应目标汇总并重置游标后重新全量同步；否则已处理过的历史 ID 不会生成新的 `_P`/`_MC` 分类。

1. 说明目标列 `last_value`、`current_value`、`last_time`、`current_time`、`update_at` 和 `stat_type` 的取值来源或默认规则；目标表的必填列必须有确定数据来源。
2. 确认源表 `ID` 是否确实唯一且非空，并在源库通过 `PRIMARY KEY` 或 `UNIQUE NOT NULL` 约束保证；当前提供的源 DDL 未声明该约束。
3. 确认 `(meter_number, frozen_time, stat_type)` 是目标业务唯一键，并在目标库创建唯一约束；当前目标 DDL 只建立了普通索引。
4. 确认源数据只追加且 ID 顺序可靠。ID 游标不能捕获旧行更新或源端删除；若有更新/删除，需要源更新时间、变更日志或 CDC。
5. 确认目标表已存在相同业务键记录时允许把本次源数量/金额累加到该记录；否则需明确覆盖或初始化目标表的策略。

连接库的 MySQL 版本与 PyMySQL/SQLAlchemy 组合兼容；本骨架没有绑定 MySQL 专有 upsert 语义，待上述规则确认后再实现具体读写策略。
