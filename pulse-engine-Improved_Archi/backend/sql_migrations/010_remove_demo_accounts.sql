DELETE FROM users
WHERE LOWER(email) = 'demo@pulseengine.local';

DELETE FROM companies
WHERE id = 'e1111111-1111-4111-8111-111111111111'
   OR (id = 'd7c253c7-3c35-47c6-8f93-b10cf50a0370' AND name = 'Local Tenant');
