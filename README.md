**FIELD_ENCRYPTION_KEY** ***set or the app will raise an error on startup. Generate one with:***    
    `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`



curl -i -X POST https://api.progstack.org/api/login -H 'Content-Type: application/json' -d '{"username":"postmaster@progstack.org","password":"@Hello2061#"}'
