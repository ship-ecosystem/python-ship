"""Portable provider definitions reviewed against PHP Ship 0964965. No PHP runtime dependency.

Update image pins, initialization and probes together with the shared provider catalog.
"""

from typing import Any

PORTABLE_SERVICES: dict[str, dict[str, Any]] = {'mariadb': {'label': 'MariaDB',
             'group': 'database',
             'dev': {'mariadb-ship_placeholder': {'image': 'mariadb:13.0',
                                                  'environment': {'MARIADB_DATABASE': '${SHIP_PLACEHOLDER_DB_DATABASE:-app}',
                                                                  'MARIADB_USER': '${SHIP_PLACEHOLDER_DB_USERNAME:-app}',
                                                                  'MARIADB_PASSWORD': '${SHIP_PLACEHOLDER_DB_PASSWORD:-secret}',
                                                                  'MARIADB_ROOT_PASSWORD': '${SHIP_PLACEHOLDER_DB_ROOT_PASSWORD:-rootsecret}'},
                                                  'volumes': ['ship-mariadb-ship_placeholder-data:/var/lib/mysql'],
                                                  'healthcheck': {'test': ['CMD',
                                                                           'healthcheck.sh',
                                                                           '--connect',
                                                                           '--innodb_initialized'],
                                                                  'interval': '5s',
                                                                  'timeout': '5s',
                                                                  'retries': 5}}},
             'prod': {'mariadb-ship_placeholder': {'image': 'mariadb:13.0',
                                                   'environment': {'MARIADB_DATABASE': '${SHIP_PLACEHOLDER_DB_DATABASE:-app}',
                                                                   'MARIADB_USER': '${SHIP_PLACEHOLDER_DB_USERNAME:-app}',
                                                                   'MARIADB_PASSWORD': '${SHIP_PLACEHOLDER_DB_PASSWORD:?set '
                                                                                       'a real value in '
                                                                                       '.env.production}',
                                                                   'MARIADB_ROOT_PASSWORD': '${SHIP_PLACEHOLDER_DB_ROOT_PASSWORD:?set '
                                                                                            'a real value in '
                                                                                            '.env.production}'},
                                                   'volumes': ['ship-mariadb-ship_placeholder-data:/var/lib/mysql'],
                                                   'healthcheck': {'test': ['CMD',
                                                                            'healthcheck.sh',
                                                                            '--connect',
                                                                            '--innodb_initialized'],
                                                                   'interval': '5s',
                                                                   'timeout': '5s',
                                                                   'retries': 5}}},
             'env': {'SHIP_PLACEHOLDER_DB_CONNECTION': 'mariadb',
                     'SHIP_PLACEHOLDER_DB_HOST': 'mariadb-ship_placeholder',
                     'SHIP_PLACEHOLDER_DB_PORT': '3306',
                     'SHIP_PLACEHOLDER_DB_DATABASE': '${SHIP_PLACEHOLDER_DB_DATABASE:-app}',
                     'SHIP_PLACEHOLDER_DB_USERNAME': '${SHIP_PLACEHOLDER_DB_USERNAME:-app}',
                     'SHIP_PLACEHOLDER_DB_PASSWORD': '${SHIP_PLACEHOLDER_DB_PASSWORD:-secret}'},
             'http': [],
             'shell': ['sh', '-c', 'exec mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"']},
 'mongodb': {'label': 'MongoDB',
             'group': 'database',
             'dev': {'mongodb-ship_placeholder': {'image': 'mongo:9.0.8',
                                                  'entrypoint': ['sh',
                                                                 '-c',
                                                                 'cat > '
                                                                 '/docker-entrypoint-initdb.d/ship-app-user.js '
                                                                 "<<'JS'\n"
                                                                 'const database = '
                                                                 'process.env.MONGO_INITDB_DATABASE;\n'
                                                                 'db.getSiblingDB(database).createUser({\n'
                                                                 '  user: process.env.SHIP_MONGODB_USERNAME,\n'
                                                                 '  pwd: process.env.SHIP_MONGODB_PASSWORD,\n'
                                                                 "  roles: [{ role: 'dbOwner', db: database }],\n"
                                                                 '});\n'
                                                                 'JS\n'
                                                                 'exec docker-entrypoint.sh mongod'],
                                                  'environment': {'MONGO_INITDB_DATABASE': '${SHIP_PLACEHOLDER_MONGODB_DATABASE:-app}',
                                                                  'MONGO_INITDB_ROOT_USERNAME': 'root',
                                                                  'MONGO_INITDB_ROOT_PASSWORD': '${SHIP_PLACEHOLDER_MONGODB_ROOT_PASSWORD:-rootsecret}',
                                                                  'SHIP_MONGODB_USERNAME': '${SHIP_PLACEHOLDER_MONGODB_USERNAME:-app}',
                                                                  'SHIP_MONGODB_PASSWORD': '${SHIP_PLACEHOLDER_MONGODB_PASSWORD:-secret}'},
                                                  'volumes': ['ship-mongodb-ship_placeholder-data:/data/db'],
                                                  'healthcheck': {'test': ['CMD-SHELL',
                                                                           'mongosh --quiet --host "$$(hostname '
                                                                           '-i)" --eval '
                                                                           '"db.adminCommand(\'ping\').ok" | grep '
                                                                           '-qx 1'],
                                                                  'interval': '5s',
                                                                  'timeout': '5s',
                                                                  'retries': 10}}},
             'prod': {'mongodb-ship_placeholder': {'image': 'mongo:9.0.8',
                                                   'entrypoint': ['sh',
                                                                  '-c',
                                                                  'cat > '
                                                                  '/docker-entrypoint-initdb.d/ship-app-user.js '
                                                                  "<<'JS'\n"
                                                                  'const database = '
                                                                  'process.env.MONGO_INITDB_DATABASE;\n'
                                                                  'db.getSiblingDB(database).createUser({\n'
                                                                  '  user: process.env.SHIP_MONGODB_USERNAME,\n'
                                                                  '  pwd: process.env.SHIP_MONGODB_PASSWORD,\n'
                                                                  "  roles: [{ role: 'dbOwner', db: database }],\n"
                                                                  '});\n'
                                                                  'JS\n'
                                                                  'exec docker-entrypoint.sh mongod'],
                                                   'environment': {'MONGO_INITDB_DATABASE': '${SHIP_PLACEHOLDER_MONGODB_DATABASE:-app}',
                                                                   'MONGO_INITDB_ROOT_USERNAME': 'root',
                                                                   'MONGO_INITDB_ROOT_PASSWORD': '${SHIP_PLACEHOLDER_MONGODB_ROOT_PASSWORD:?set '
                                                                                                 'a real value in '
                                                                                                 '.env.production}',
                                                                   'SHIP_MONGODB_USERNAME': '${SHIP_PLACEHOLDER_MONGODB_USERNAME:-app}',
                                                                   'SHIP_MONGODB_PASSWORD': '${SHIP_PLACEHOLDER_MONGODB_PASSWORD:?set '
                                                                                            'a real value in '
                                                                                            '.env.production}'},
                                                   'volumes': ['ship-mongodb-ship_placeholder-data:/data/db'],
                                                   'healthcheck': {'test': ['CMD-SHELL',
                                                                            'mongosh --quiet --host "$$(hostname '
                                                                            '-i)" --eval '
                                                                            '"db.adminCommand(\'ping\').ok" | '
                                                                            'grep -qx 1'],
                                                                   'interval': '5s',
                                                                   'timeout': '5s',
                                                                   'retries': 10}}},
             'env': {'SHIP_PLACEHOLDER_MONGODB_URI': 'mongodb://${SHIP_PLACEHOLDER_MONGODB_USERNAME:-app}:${SHIP_PLACEHOLDER_MONGODB_PASSWORD:-secret}@mongodb-ship_placeholder:27017/${SHIP_PLACEHOLDER_MONGODB_DATABASE:-app}',
                     'SHIP_PLACEHOLDER_MONGODB_DATABASE': '${SHIP_PLACEHOLDER_MONGODB_DATABASE:-app}'},
             'http': [],
             'shell': ['sh',
                       '-c',
                       'exec mongosh -u "$SHIP_MONGODB_USERNAME" -p "$SHIP_MONGODB_PASSWORD" '
                       '--authenticationDatabase "$MONGO_INITDB_DATABASE" "$MONGO_INITDB_DATABASE"']},
 'sqlserver': {'label': 'SQL Server',
               'group': 'database',
               'dev': {'sqlserver-ship_placeholder': {'image': 'mcr.microsoft.com/mssql/server:2025-CU11-ubuntu-24.04',
                                                      'platform': 'linux/amd64',
                                                      'entrypoint': ['/bin/bash',
                                                                     '-c',
                                                                     'case "$$(printf \'%s\' '
                                                                     '"$$SHIP_MSSQL_USERNAME" | tr \'[:upper:]\' '
                                                                     '\'[:lower:]\')" in\n'
                                                                     '  sa) echo \'ship: DB_USERNAME is "sa", SQL '
                                                                     'Server\'"\'"\'s built-in admin login (its '
                                                                     'password is DB_ROOT_PASSWORD). Pick a '
                                                                     "different username.' >&2; exit 1 ;;\n"
                                                                     'esac\n'
                                                                     '/opt/mssql/bin/launch_sqlservr.sh '
                                                                     '/opt/mssql/bin/sqlservr &\n'
                                                                     'pid=$$!\n'
                                                                     "# launch_sqlservr.sh doesn't forward "
                                                                     'signals to its sqlservr child, so the stop '
                                                                     'signal goes to\n'
                                                                     '# SQL Server itself (the oldest sqlservr, '
                                                                     'which coordinates shutdown), not to the '
                                                                     'wrapper.\n'
                                                                     "trap 'pkill -TERM -o -x sqlservr' TERM INT\n"
                                                                     'sql() { /opt/mssql-tools18/bin/sqlcmd -C -b '
                                                                     '-x -S localhost -U sa -P '
                                                                     '"$$MSSQL_SA_PASSWORD" "$$@"; }\n'
                                                                     'lit() { printf \'%s\' "$$1" | sed '
                                                                     '"s/\'/\'\'/g"; }\n'
                                                                     "until sql -l 2 -Q 'SELECT 1' >/dev/null "
                                                                     '2>&1; do\n'
                                                                     '  kill -0 $$pid 2>/dev/null || { echo '
                                                                     "'ship: SQL Server exited during startup; "
                                                                     'see the log above (a weak DB_ROOT_PASSWORD '
                                                                     "is the usual cause).' >&2; exit 1; }\n"
                                                                     '  sleep 1\n'
                                                                     'done\n'
                                                                     'if ! sql -Q "SET NOCOUNT ON;\n'
                                                                     "  DECLARE @db sysname = N'$$(lit "
                                                                     '"$$SHIP_MSSQL_DATABASE")\', @user sysname = '
                                                                     'N\'$$(lit "$$SHIP_MSSQL_USERNAME")\',\n'
                                                                     "    @pw nvarchar(128) = N'$$(lit "
                                                                     '"$$SHIP_MSSQL_PASSWORD")\', @sql '
                                                                     'nvarchar(max);\n'
                                                                     '  IF DB_ID(@db) IS NULL BEGIN SET @sql = '
                                                                     "N'CREATE DATABASE ' + QUOTENAME(@db); EXEC "
                                                                     '(@sql); END;\n'
                                                                     '  IF SUSER_ID(@user) IS NULL BEGIN\n'
                                                                     "    SET @sql = N'CREATE LOGIN ' + "
                                                                     "QUOTENAME(@user) + N' WITH PASSWORD = ' + "
                                                                     "QUOTENAME(@pw, '''') + N', DEFAULT_DATABASE "
                                                                     "= ' + QUOTENAME(@db);\n"
                                                                     '    EXEC (@sql);\n'
                                                                     '  END;\n'
                                                                     "  IF IS_SRVROLEMEMBER('sysadmin', @user) = "
                                                                     '0 BEGIN\n'
                                                                     "    SET @sql = N'USE ' + QUOTENAME(@db) + "
                                                                     "N'; IF USER_ID(' + QUOTENAME(@user, '''') + "
                                                                     "N') IS NULL CREATE USER '\n"
                                                                     "      + QUOTENAME(@user) + N' FOR LOGIN ' + "
                                                                     "QUOTENAME(@user) + N'; ALTER ROLE db_owner "
                                                                     "ADD MEMBER ' + QUOTENAME(@user) + N';';\n"
                                                                     '    EXEC (@sql);\n'
                                                                     '  END;"; then\n'
                                                                     "  echo 'ship: could not create the app "
                                                                     'database or login; see the error above (a '
                                                                     'DB_PASSWORD that fails the password policy '
                                                                     "is the usual cause).' >&2\n"
                                                                     '  pkill -TERM -o -x sqlservr; wait $$pid; '
                                                                     'exit 1\n'
                                                                     'fi\n'
                                                                     'wait $$pid; status=$$?\n'
                                                                     '# A trapped signal interrupts the first '
                                                                     'wait; the second lets SQL Server finish '
                                                                     'shutting down.\n'
                                                                     'if kill -0 $$pid 2>/dev/null; then wait '
                                                                     '$$pid; status=$$?; fi\n'
                                                                     'exit $$status'],
                                                      'stop_grace_period': '30s',
                                                      'environment': {'ACCEPT_EULA': 'Y',
                                                                      'MSSQL_PID': 'Developer',
                                                                      'MSSQL_SA_PASSWORD': '${SHIP_PLACEHOLDER_DB_ROOT_PASSWORD:-ShipRoot123}',
                                                                      'SHIP_MSSQL_DATABASE': '${SHIP_PLACEHOLDER_DB_DATABASE:-app}',
                                                                      'SHIP_MSSQL_USERNAME': '${SHIP_PLACEHOLDER_DB_USERNAME:-app}',
                                                                      'SHIP_MSSQL_PASSWORD': '${SHIP_PLACEHOLDER_DB_PASSWORD:-ShipSecret123}'},
                                                      'volumes': ['ship-sqlserver-ship_placeholder-data:/var/opt/mssql'],
                                                      'healthcheck': {'test': ['CMD-SHELL',
                                                                               '/opt/mssql-tools18/bin/sqlcmd -C '
                                                                               '-l 2 -S localhost -U '
                                                                               '"$$SHIP_MSSQL_USERNAME" -P '
                                                                               '"$$SHIP_MSSQL_PASSWORD" -d '
                                                                               '"$$SHIP_MSSQL_DATABASE" -Q '
                                                                               '"SELECT 1" >/dev/null'],
                                                                      'interval': '5s',
                                                                      'timeout': '5s',
                                                                      'retries': 20,
                                                                      'start_period': '20s'}}},
               'prod': {'sqlserver-ship_placeholder': {'image': 'mcr.microsoft.com/mssql/server:2025-CU11-ubuntu-24.04',
                                                       'platform': 'linux/amd64',
                                                       'entrypoint': ['/bin/bash',
                                                                      '-c',
                                                                      'case "$$(printf \'%s\' '
                                                                      '"$$SHIP_MSSQL_USERNAME" | tr \'[:upper:]\' '
                                                                      '\'[:lower:]\')" in\n'
                                                                      '  sa) echo \'ship: DB_USERNAME is "sa", '
                                                                      'SQL Server\'"\'"\'s built-in admin login '
                                                                      '(its password is DB_ROOT_PASSWORD). Pick a '
                                                                      "different username.' >&2; exit 1 ;;\n"
                                                                      'esac\n'
                                                                      '/opt/mssql/bin/launch_sqlservr.sh '
                                                                      '/opt/mssql/bin/sqlservr &\n'
                                                                      'pid=$$!\n'
                                                                      "# launch_sqlservr.sh doesn't forward "
                                                                      'signals to its sqlservr child, so the stop '
                                                                      'signal goes to\n'
                                                                      '# SQL Server itself (the oldest sqlservr, '
                                                                      'which coordinates shutdown), not to the '
                                                                      'wrapper.\n'
                                                                      "trap 'pkill -TERM -o -x sqlservr' TERM "
                                                                      'INT\n'
                                                                      'sql() { /opt/mssql-tools18/bin/sqlcmd -C '
                                                                      '-b -x -S localhost -U sa -P '
                                                                      '"$$MSSQL_SA_PASSWORD" "$$@"; }\n'
                                                                      'lit() { printf \'%s\' "$$1" | sed '
                                                                      '"s/\'/\'\'/g"; }\n'
                                                                      "until sql -l 2 -Q 'SELECT 1' >/dev/null "
                                                                      '2>&1; do\n'
                                                                      '  kill -0 $$pid 2>/dev/null || { echo '
                                                                      "'ship: SQL Server exited during startup; "
                                                                      'see the log above (a weak DB_ROOT_PASSWORD '
                                                                      "is the usual cause).' >&2; exit 1; }\n"
                                                                      '  sleep 1\n'
                                                                      'done\n'
                                                                      'if ! sql -Q "SET NOCOUNT ON;\n'
                                                                      "  DECLARE @db sysname = N'$$(lit "
                                                                      '"$$SHIP_MSSQL_DATABASE")\', @user sysname '
                                                                      '= N\'$$(lit "$$SHIP_MSSQL_USERNAME")\',\n'
                                                                      "    @pw nvarchar(128) = N'$$(lit "
                                                                      '"$$SHIP_MSSQL_PASSWORD")\', @sql '
                                                                      'nvarchar(max);\n'
                                                                      '  IF DB_ID(@db) IS NULL BEGIN SET @sql = '
                                                                      "N'CREATE DATABASE ' + QUOTENAME(@db); EXEC "
                                                                      '(@sql); END;\n'
                                                                      '  IF SUSER_ID(@user) IS NULL BEGIN\n'
                                                                      "    SET @sql = N'CREATE LOGIN ' + "
                                                                      "QUOTENAME(@user) + N' WITH PASSWORD = ' + "
                                                                      "QUOTENAME(@pw, '''') + N', "
                                                                      "DEFAULT_DATABASE = ' + QUOTENAME(@db);\n"
                                                                      '    EXEC (@sql);\n'
                                                                      '  END;\n'
                                                                      "  IF IS_SRVROLEMEMBER('sysadmin', @user) = "
                                                                      '0 BEGIN\n'
                                                                      "    SET @sql = N'USE ' + QUOTENAME(@db) + "
                                                                      "N'; IF USER_ID(' + QUOTENAME(@user, '''') "
                                                                      "+ N') IS NULL CREATE USER '\n"
                                                                      "      + QUOTENAME(@user) + N' FOR LOGIN ' "
                                                                      "+ QUOTENAME(@user) + N'; ALTER ROLE "
                                                                      "db_owner ADD MEMBER ' + QUOTENAME(@user) + "
                                                                      "N';';\n"
                                                                      '    EXEC (@sql);\n'
                                                                      '  END;"; then\n'
                                                                      "  echo 'ship: could not create the app "
                                                                      'database or login; see the error above (a '
                                                                      'DB_PASSWORD that fails the password policy '
                                                                      "is the usual cause).' >&2\n"
                                                                      '  pkill -TERM -o -x sqlservr; wait $$pid; '
                                                                      'exit 1\n'
                                                                      'fi\n'
                                                                      'wait $$pid; status=$$?\n'
                                                                      '# A trapped signal interrupts the first '
                                                                      'wait; the second lets SQL Server finish '
                                                                      'shutting down.\n'
                                                                      'if kill -0 $$pid 2>/dev/null; then wait '
                                                                      '$$pid; status=$$?; fi\n'
                                                                      'exit $$status'],
                                                       'stop_grace_period': '30s',
                                                       'environment': {'ACCEPT_EULA': 'Y',
                                                                       'MSSQL_PID': '${SHIP_PLACEHOLDER_MSSQL_PID:-Developer}',
                                                                       'MSSQL_SA_PASSWORD': '${SHIP_PLACEHOLDER_DB_ROOT_PASSWORD:?set '
                                                                                            'a real value in '
                                                                                            '.env.production}',
                                                                       'SHIP_MSSQL_DATABASE': '${SHIP_PLACEHOLDER_DB_DATABASE:-app}',
                                                                       'SHIP_MSSQL_USERNAME': '${SHIP_PLACEHOLDER_DB_USERNAME:-app}',
                                                                       'SHIP_MSSQL_PASSWORD': '${SHIP_PLACEHOLDER_DB_PASSWORD:?set '
                                                                                              'a real value in '
                                                                                              '.env.production}'},
                                                       'volumes': ['ship-sqlserver-ship_placeholder-data:/var/opt/mssql'],
                                                       'healthcheck': {'test': ['CMD-SHELL',
                                                                                '/opt/mssql-tools18/bin/sqlcmd -C '
                                                                                '-l 2 -S localhost -U '
                                                                                '"$$SHIP_MSSQL_USERNAME" -P '
                                                                                '"$$SHIP_MSSQL_PASSWORD" -d '
                                                                                '"$$SHIP_MSSQL_DATABASE" -Q '
                                                                                '"SELECT 1" >/dev/null'],
                                                                       'interval': '5s',
                                                                       'timeout': '5s',
                                                                       'retries': 20,
                                                                       'start_period': '20s'}}},
               'env': {'SHIP_PLACEHOLDER_DB_CONNECTION': 'sqlsrv',
                       'SHIP_PLACEHOLDER_DB_HOST': 'sqlserver-ship_placeholder',
                       'SHIP_PLACEHOLDER_DB_PORT': '1433',
                       'SHIP_PLACEHOLDER_DB_DATABASE': '${SHIP_PLACEHOLDER_DB_DATABASE:-app}',
                       'SHIP_PLACEHOLDER_DB_USERNAME': '${SHIP_PLACEHOLDER_DB_USERNAME:-app}',
                       'SHIP_PLACEHOLDER_DB_PASSWORD': '${SHIP_PLACEHOLDER_DB_PASSWORD:-ShipSecret123}',
                       'SHIP_PLACEHOLDER_DB_URL': 'sqlsrv://${SHIP_PLACEHOLDER_DB_USERNAME:-app}:${SHIP_PLACEHOLDER_DB_PASSWORD:-ShipSecret123}@sqlserver-ship_placeholder:1433/${SHIP_PLACEHOLDER_DB_DATABASE:-app}?trust_server_certificate=true'},
               'http': [],
               'shell': ['sh',
                         '-c',
                         'exec /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U "$SHIP_MSSQL_USERNAME" -P '
                         '"$SHIP_MSSQL_PASSWORD" -d "$SHIP_MSSQL_DATABASE"']},
 'valkey': {'label': 'Valkey (Redis-compatible)',
            'group': 'cache',
            'dev': {'valkey-ship_placeholder': {'image': 'valkey/valkey:9.2.0-alpine',
                                                'command': ['valkey-server', '--save', '60 1'],
                                                'volumes': ['ship-valkey-ship_placeholder-data:/data'],
                                                'healthcheck': {'test': ['CMD', 'valkey-cli', 'ping'],
                                                                'interval': '5s',
                                                                'timeout': '5s',
                                                                'retries': 5}}},
            'prod': {'valkey-ship_placeholder': {'image': 'valkey/valkey:9.2.0-alpine',
                                                 'command': ['valkey-server', '--save', '60 1'],
                                                 'volumes': ['ship-valkey-ship_placeholder-data:/data'],
                                                 'healthcheck': {'test': ['CMD', 'valkey-cli', 'ping'],
                                                                 'interval': '5s',
                                                                 'timeout': '5s',
                                                                 'retries': 5}}},
            'env': {'SHIP_PLACEHOLDER_REDIS_HOST': 'valkey-ship_placeholder',
                    'SHIP_PLACEHOLDER_REDIS_PORT': '6379'},
            'http': [],
            'shell': None},
 'memcached': {'label': 'Memcached (cache only)',
               'group': 'cache',
               'dev': {'memcached-ship_placeholder': {'image': 'memcached:1.6.45-alpine',
                                                      'healthcheck': {'test': ['CMD-SHELL',
                                                                               'echo version | nc -w 2 127.0.0.1 '
                                                                               '11211 | grep -q "^VERSION"'],
                                                                      'interval': '5s',
                                                                      'timeout': '5s',
                                                                      'retries': 5}}},
               'prod': {'memcached-ship_placeholder': {'image': 'memcached:1.6.45-alpine',
                                                       'healthcheck': {'test': ['CMD-SHELL',
                                                                                'echo version | nc -w 2 127.0.0.1 '
                                                                                '11211 | grep -q "^VERSION"'],
                                                                       'interval': '5s',
                                                                       'timeout': '5s',
                                                                       'retries': 5}}},
               'env': {'SHIP_PLACEHOLDER_MEMCACHED_HOST': 'memcached-ship_placeholder',
                       'SHIP_PLACEHOLDER_MEMCACHED_PORT': '11211'},
               'http': [],
               'shell': None},
 'elasticsearch': {'label': 'Elasticsearch',
                   'group': 'search',
                   'dev': {'elasticsearch-ship_placeholder': {'image': 'docker.elastic.co/elasticsearch/elasticsearch:9.5.7',
                                                              'environment': {'discovery.type': 'single-node',
                                                                              'xpack.security.enabled': 'true',
                                                                              'xpack.security.http.ssl.enabled': 'false',
                                                                              'xpack.security.transport.ssl.enabled': 'false',
                                                                              'ELASTIC_PASSWORD': '${SHIP_PLACEHOLDER_ELASTICSEARCH_PASSWORD:-secret}',
                                                                              'ES_JAVA_OPTS': '-Xms1g -Xmx1g'},
                                                              'volumes': ['ship-elasticsearch-ship_placeholder-data:/usr/share/elasticsearch/data'],
                                                              'healthcheck': {'test': ['CMD-SHELL',
                                                                                       'curl -fsS -u '
                                                                                       '"elastic:$$ELASTIC_PASSWORD" '
                                                                                       '"http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=5s" '
                                                                                       '>/dev/null'],
                                                                              'interval': '10s',
                                                                              'timeout': '10s',
                                                                              'retries': 12,
                                                                              'start_period': '30s'}}},
                   'prod': {'elasticsearch-ship_placeholder': {'image': 'docker.elastic.co/elasticsearch/elasticsearch:9.5.7',
                                                               'environment': {'discovery.type': 'single-node',
                                                                               'xpack.security.enabled': 'true',
                                                                               'xpack.security.http.ssl.enabled': 'false',
                                                                               'xpack.security.transport.ssl.enabled': 'false',
                                                                               'ELASTIC_PASSWORD': '${SHIP_PLACEHOLDER_ELASTICSEARCH_PASSWORD:?set '
                                                                                                   'a real value '
                                                                                                   'in '
                                                                                                   '.env.production}'},
                                                               'volumes': ['ship-elasticsearch-ship_placeholder-data:/usr/share/elasticsearch/data'],
                                                               'healthcheck': {'test': ['CMD-SHELL',
                                                                                        'curl -fsS -u '
                                                                                        '"elastic:$$ELASTIC_PASSWORD" '
                                                                                        '"http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=5s" '
                                                                                        '>/dev/null'],
                                                                               'interval': '10s',
                                                                               'timeout': '10s',
                                                                               'retries': 12,
                                                                               'start_period': '30s'}}},
                   'env': {'SHIP_PLACEHOLDER_ELASTICSEARCH_HOST': 'http://elasticsearch-ship_placeholder:9200',
                           'SHIP_PLACEHOLDER_ELASTICSEARCH_USERNAME': 'elastic',
                           'SHIP_PLACEHOLDER_ELASTICSEARCH_PASSWORD': '${SHIP_PLACEHOLDER_ELASTICSEARCH_PASSWORD:-secret}'},
                   'http': [],
                   'shell': None},
 'typesense': {'label': 'Typesense',
               'group': 'search',
               'dev': {'typesense-ship_placeholder': {'image': 'typesense/typesense:30.2',
                                                      'environment': {'TYPESENSE_API_KEY': '${SHIP_PLACEHOLDER_TYPESENSE_API_KEY:-shipsearchkey}',
                                                                      'TYPESENSE_DATA_DIR': '/data'},
                                                      'volumes': ['ship-typesense-ship_placeholder-data:/data'],
                                                      'healthcheck': {'test': ['CMD',
                                                                               'bash',
                                                                               '-c',
                                                                               'exec 3<>/dev/tcp/127.0.0.1/8108 '
                                                                               '&& printf "GET /health '
                                                                               'HTTP/1.0\\r\\n\\r\\n" >&3 && grep '
                                                                               '-q \'"ok":true\' <&3'],
                                                                      'interval': '5s',
                                                                      'timeout': '5s',
                                                                      'retries': 12}}},
               'prod': {'typesense-ship_placeholder': {'image': 'typesense/typesense:30.2',
                                                       'environment': {'TYPESENSE_API_KEY': '${SHIP_PLACEHOLDER_TYPESENSE_API_KEY:?set '
                                                                                            'a real value in '
                                                                                            '.env.production}',
                                                                       'TYPESENSE_DATA_DIR': '/data'},
                                                       'volumes': ['ship-typesense-ship_placeholder-data:/data'],
                                                       'healthcheck': {'test': ['CMD',
                                                                                'bash',
                                                                                '-c',
                                                                                'exec 3<>/dev/tcp/127.0.0.1/8108 '
                                                                                '&& printf "GET /health '
                                                                                'HTTP/1.0\\r\\n\\r\\n" >&3 && '
                                                                                'grep -q \'"ok":true\' <&3'],
                                                                       'interval': '5s',
                                                                       'timeout': '5s',
                                                                       'retries': 12}}},
               'env': {'SHIP_PLACEHOLDER_TYPESENSE_HOST': 'typesense-ship_placeholder',
                       'SHIP_PLACEHOLDER_TYPESENSE_PORT': '8108',
                       'SHIP_PLACEHOLDER_TYPESENSE_PROTOCOL': 'http',
                       'SHIP_PLACEHOLDER_TYPESENSE_API_KEY': '${SHIP_PLACEHOLDER_TYPESENSE_API_KEY:-shipsearchkey}'},
               'http': [],
               'shell': None},
 'cypress': {'label': 'Cypress',
             'group': 'testing',
             'dev': {'cypress': {'image': 'cypress/included:16.3.0',
                                 'entrypoint': ['sleep', 'infinity'],
                                 'init': True,
                                 'working_dir': '/e2e',
                                 'volumes': ['.:/e2e'],
                                 'shm_size': '2gb'}},
             'prod': [],
             'env': {'CYPRESS_INSTALL_BINARY': '0'},
             'http': [],
             'shell': None},
 'centrifugo': {'label': 'Centrifugo (WebSockets, any framework)',
                'group': 'broadcasting',
                'dev': {'centrifugo': {'image': 'centrifugo/centrifugo:v6.10.3',
                                       'environment': {'CENTRIFUGO_HTTP_API_KEY': '${CENTRIFUGO_API_KEY:-ship-centrifugo-api-key}',
                                                       'CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY': '${CENTRIFUGO_TOKEN_SECRET:-ship-centrifugo-token-secret}',
                                                       'CENTRIFUGO_CLIENT_ALLOWED_ORIGINS': '${CENTRIFUGO_ALLOWED_ORIGINS:-*}',
                                                       'CENTRIFUGO_HEALTH_ENABLED': 'true',
                                                       'CENTRIFUGO_ENGINE_TYPE': '${CENTRIFUGO_ENGINE:-memory}',
                                                       'CENTRIFUGO_ENGINE_REDIS_ADDRESS': '${CENTRIFUGO_REDIS_ADDRESS:-}',
                                                       'CENTRIFUGO_ADMIN_ENABLED': 'true',
                                                       'CENTRIFUGO_ADMIN_PASSWORD': '${CENTRIFUGO_ADMIN_PASSWORD:-ship}',
                                                       'CENTRIFUGO_ADMIN_SECRET': 'ship-centrifugo-admin-secret'},
                                       'ports': ['127.0.0.1:${CENTRIFUGO_PORT:-8001}:8000'],
                                       'healthcheck': {'test': ['CMD',
                                                                'wget',
                                                                '-qO-',
                                                                'http://127.0.0.1:8000/health'],
                                                       'interval': '5s',
                                                       'timeout': '5s',
                                                       'retries': 5}}},
                'prod': {'centrifugo': {'image': 'centrifugo/centrifugo:v6.10.3',
                                        'environment': {'CENTRIFUGO_HTTP_API_KEY': '${CENTRIFUGO_API_KEY:?set a '
                                                                                   'real value in '
                                                                                   '.env.production}',
                                                        'CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY': '${CENTRIFUGO_TOKEN_SECRET:?set '
                                                                                                   'a real value '
                                                                                                   'in '
                                                                                                   '.env.production}',
                                                        'CENTRIFUGO_CLIENT_ALLOWED_ORIGINS': '${CENTRIFUGO_ALLOWED_ORIGINS:?set '
                                                                                             'a real value in '
                                                                                             '.env.production}',
                                                        'CENTRIFUGO_HEALTH_ENABLED': 'true',
                                                        'CENTRIFUGO_ENGINE_TYPE': '${CENTRIFUGO_ENGINE:-memory}',
                                                        'CENTRIFUGO_ENGINE_REDIS_ADDRESS': '${CENTRIFUGO_REDIS_ADDRESS:-}'},
                                        'ports': ['${CENTRIFUGO_PORT:-8001}:8000'],
                                        'healthcheck': {'test': ['CMD',
                                                                 'wget',
                                                                 '-qO-',
                                                                 'http://127.0.0.1:8000/health'],
                                                        'interval': '5s',
                                                        'timeout': '5s',
                                                        'retries': 5}}},
                'env': {'CENTRIFUGO_URL': 'http://centrifugo:8000',
                        'CENTRIFUGO_API_KEY': '${CENTRIFUGO_API_KEY:-ship-centrifugo-api-key}',
                        'CENTRIFUGO_TOKEN_SECRET': '${CENTRIFUGO_TOKEN_SECRET:-ship-centrifugo-token-secret}',
                        'VITE_CENTRIFUGO_URL': 'ws://localhost:${CENTRIFUGO_PORT:-8001}/connection/websocket'},
                'http': [{'port': 8000,
                          'role': 'websocket',
                          'health': '/health',
                          'urlSetting': 'VITE_CENTRIFUGO_URL'}],
                'shell': None}}
