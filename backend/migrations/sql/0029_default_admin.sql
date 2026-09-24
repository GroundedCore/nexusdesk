-- nexusdesk deployment seed: admin / nexusdesk (must change on first login).
-- Run after schema migrations. Set nexusdesk.tenant_id for non-local tenants.
-- Existing local administrators and changed passwords are never overwritten.
DO $seed$
DECLARE
    target_tenant text := COALESCE(NULLIF(current_setting('nexusdesk.tenant_id', true), ''), 'local');
    administrator uuid := gen_random_uuid();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(target_tenant || chr(58) || 'local-admin-init', 0));
    IF EXISTS (SELECT 1 FROM local_admin_credentials WHERE tenant_id=target_tenant) THEN
        RETURN;
    END IF;
    INSERT INTO enterprise_users(id,tenant_id,name,role)
        VALUES(administrator,target_tenant,'本地应急管理员','admin');
    INSERT INTO local_admin_credentials(user_id,tenant_id,username,password_hash,must_change_password)
        VALUES(administrator,target_tenant,'admin','pbkdf2_sha256$600000$7cbf7c552f49eb265089f859bb2b84ec$5a2da5b616abcbb6ab11b249d3280d1d2309d4d5db0898a2f31c0d5d35e5fc82',true);
    INSERT INTO audit_records(tenant_id,actor,action,resource,details)
        VALUES(target_tenant,'deployment','local_admin.initialized',administrator::text,'{"source":"deployment_sql"}');
END
$seed$;
