import hashlib
salt = "my-secret-salt-123"   # must match your .env
username = "admin"
password = "mypassword"

hash_value = hashlib.sha256(f"{salt}:{username}:{password}".encode()).hexdigest()
print(hash_value)