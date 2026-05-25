
# Create temp db
```bash
docker run --name temp-postgres -e PGDATA=/etc/postgresql/data -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres -d -p 5432:5432 postgres:18.4
```

# Apply DB migrations
```bash
docker run --rm --network host -v ./migration:/flyway/sql flyway/flyway -url=jdbc:postgresql://localhost:5432/postgres -user=postgres -password=postgres migrate
```

# Stop the database
```bash
docker stop temp-postgres
```

# Commit the database container as a new image:
```bash
docker commit temp-postgres localhost:5000/football-db:postgres-18.4
```

# Push
```bash
docker push localhost:5000/football-db:postgres-18.4
```

# Remove container again
```bash
docker rm temp-postgres
```