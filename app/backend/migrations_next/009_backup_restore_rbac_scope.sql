UPDATE roles
SET
    description = 'Can add, edit, delete and refresh media without collection backup/import/export permissions',
    updated_at = now()
WHERE key = 'media_editor';

DELETE FROM role_permissions rp
USING roles r
WHERE rp.role_id = r.id
  AND r.key IN ('media_editor', 'media_fan', 'media_viewer')
  AND rp.permission_key IN (
      'admin.backup',
      'admin.restore_functional',
      'admin.restore_full',
      'collection.export_functional',
      'collection.import',
      'security.export_security',
      'security.restore_security'
  );
