-- 本地/生产 PostgreSQL 的默认初始化脚本。
--
-- 由官方 postgres 镜像在**数据卷为空时**自动执行（/docker-entrypoint-initdb.d/），
-- 建库、建用户已由 POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB 完成，
-- 这里只补应用依赖的库级设置。
--
-- 注意：
-- 1) 本文件不接收外部参数，库名一律用 current_database()，因此同一份脚本
--    在 hanyue / hanyue_dev 等不同库名下都能直接跑。
-- 2) 所有动作都做成幂等的：ON_ERROR_STOP=1 下任何失败都会让容器启动失败，
--    所以可能不存在的扩展必须先探测再建。
-- 3) 改了本文件不会作用于已有数据卷——需要 dev-db.sh reset 或手工重跑。

\set ON_ERROR_STOP on

-- 时区：应用写库用 UTC，展示按用户时区；库级时区统一到 Asia/Shanghai，
-- 便于直接用 psql 看数据时与业务时间对齐（不影响 TIMESTAMPTZ 的存储值）。
DO $$
BEGIN
  EXECUTE format(
    'ALTER DATABASE %I SET timezone TO %L', current_database(), 'Asia/Shanghai'
  );
  -- 卡住的事务会一直占着连接与锁；开发机上尤其容易被调试断点拖住
  EXECUTE format(
    'ALTER DATABASE %I SET idle_in_transaction_session_timeout TO %L',
    current_database(), '60s'
  );
  -- 慢查询保护：超过 30s 的语句直接取消，避免一条误写的查询拖垮整个本地库
  EXECUTE format(
    'ALTER DATABASE %I SET statement_timeout TO %L', current_database(), '30s'
  );
END
$$;

-- pg_trgm：标题/正文的 ILIKE 模糊搜索靠它走索引而不是全表扫。
-- alpine 镜像自带 contrib，但不同版本可能有差异，因此先探测再建。
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm') THEN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    RAISE NOTICE 'pg_trgm 已就绪';
  ELSE
    RAISE NOTICE 'pg_trgm 不可用（镜像缺 contrib），已跳过；模糊搜索仍可用但会全表扫描';
  END IF;
END
$$;

-- 建库确认，方便在 `docker compose logs db` 里确认初始化确实跑过
DO $$
BEGIN
  RAISE NOTICE '数据库 % 初始化完成（timezone=Asia/Shanghai, statement_timeout=30s）',
    current_database();
END
$$;
