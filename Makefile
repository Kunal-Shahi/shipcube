# Local stack (docs/LOCAL.md). Every target wraps deploy/local/dev.
.PHONY: up down reset migrate seed smoke logs ps
up:      ; deploy/local/dev up
down:    ; deploy/local/dev down
reset:   ; deploy/local/dev reset
migrate: ; deploy/local/dev migrate
seed:    ; deploy/local/dev seed
smoke:   ; deploy/local/dev smoke
logs:    ; deploy/local/dev logs
ps:      ; deploy/local/dev ps
