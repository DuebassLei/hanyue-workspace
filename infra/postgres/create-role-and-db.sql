-- 手工安装 PostgreSQL（非 Docker）时的建库建用户脚本。
--
-- 放在 init/ 之外是刻意的：/docker-entrypoint-initdb.d/ 里的脚本会被容器自动执行，
-- 而这里需要外部传入密码，自动执行会因缺参数失败并让容器起不来。
--
-- 用法：
--   sudo -u postgres psql -v app_password='换成你的密码' -f create-role-and-db.sql
--
-- 可选参数（不传则用默认值）：
--   -v app_user=hanyue
--   -v app_db=hanyue
--   -v app_password=...        （必填）
--
-- 幂等：角色/库已存在时只更新密码与归属，不会报错，也不会丢数据。

\set ON_ERROR_STOP on

\if :{?app_user}
\else
  \set app_user hanyue
\endif

\if :{?app_db}
\else
  \set app_db hanyue
\endif

\if :{?app_password}
\else
  \echo '缺少 app_password：请用 -v app_password=... 传入'
  \quit
\endif

-- 角色：存在则改密码（便于重置），不存在则创建
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password')
\gexec

-- 数据库：存在则只校正归属，不存在才创建
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db')
\gexec

SELECT format('ALTER DATABASE %I OWNER TO %I', :'app_db', :'app_user')
\gexec

GRANT ALL PRIVILEGES ON DATABASE :"app_db" TO :"app_user";

\echo '完成。应用连接串形如：'
\echo '  postgresql+psycopg://<user>:<password>@127.0.0.1:5432/<db>'
\echo '接着执行 infra/postgres/init/01-init.sql 可补齐时区与扩展设置（需连到该库）：'
\echo '  psql -U <user> -d <db> -f init/01-init.sql'
