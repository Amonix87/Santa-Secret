# Secret Santa

Petit site auto-hébergé : pas de compte, pas d'e-mail. L'organisateur saisit la liste
des participants, règle les chances de chaque paire, lance le tirage, puis envoie à chacun
son lien personnel.

## Démarrage

```bash
docker compose up -d --build
```

- Organisateur : http://localhost:8000/admin (mot de passe = `ADMIN_PASSWORD`)
- Participants : lien personnel `/p/<code>` affiché dans l'espace organisateur

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `ADMIN_PASSWORD` | Mot de passe organisateur. Si absent, un mot de passe temporaire est écrit dans les logs. |
| `PARTICIPANTS` | Liste de départ, séparée par des virgules. Lue uniquement si la base est vide. |
| `COUPLES` | Groupes à faibles chances entre eux : `Alice+Bob, Chloé+David` (3 noms ou plus acceptés : `A+B+C`). |
| `COUPLE_NOTE` | Note appliquée entre membres d'un groupe, de 0 à 9 (défaut `1`). |
| `PUBLIC_URL` | Adresse publique utilisée pour construire les liens (sinon celle de la requête). |
| `BEHIND_PROXY=1` | À activer derrière un reverse proxy (respect de `X-Forwarded-*`). |
| `COOKIE_SECURE=1` | Cookie de session réservé au HTTPS. |
| `APP_TZ` | Fuseau horaire affiché (défaut `Europe/Paris`). |
| `SECRET_KEY` | Clé de session. Sinon générée et stockée dans `/data`. |

Les données (base SQLite) sont dans le volume `/data`. Pour repartir de zéro :
`docker compose down -v`.

## Réglage des chances : les notes

Chaque case « ligne → colonne » reçoit une note entière de 0 à 10 (5 par défaut) :

| Note | Effet |
|---|---|
| 0 | Interdit |
| 1 à 4 | Moins de chances (1 ≈ 5 fois moins qu'une case à 5) |
| 5 | Neutre |
| 6 à 9 | Plus de chances (9 ≈ 5 fois plus qu'une case à 5) |
| 10 | Certain (100 %) : le donneur offre forcément à ce receveur ; l'inverse garde sa note |

De 1 à 9, chaque point de plus multiplie les chances par 1,5. Le tirage suit la règle : la probabilité
d'une répartition complète est proportionnelle au produit des poids de ses paires (parmi les
répartitions valides). Le bouton « Voir les probabilités » simule 2 000 tirages et affiche la
probabilité réelle de chaque paire : c'est la meilleure façon de calibrer les notes.

Deux façons de noter : le bloc « Régler une paire » (Alice → Emma 10, Emma → Alice 5) ou
la grille, qu'on peint avec un pinceau de 0 à 10. Les bases créées avec une version
précédente sont converties automatiquement.

**Note 10** : Alice → Bob à 10 signifie qu'Alice offrira forcément à Bob. Bob reste libre :
Bob → Alice garde sa note (même avec « Éviter les échanges réciproques », qui
ne s'applique pas à une paire à 10). Une seule case à 10 par ligne et par colonne ; les
cases écartées sont grisées dans la grille.

**Couples et groupes** : le champ « Couples ou groupes » (ou la variable `COUPLES`) applique une
note entre les membres d'un groupe, dans les deux sens. Par défaut 1 (environ 3 % de chances de
se tirer), réglable dans le champ « Note entre eux » ou avec `COUPLE_NOTE`. Avec 0, ils ne se
tirent jamais. Valider à nouveau un groupe avec une autre note remplace l'ancienne.

Option « Éviter les échanges réciproques » : interdit qu'Alice offre à Bob pendant que Bob
offre à Alice.

## Liens personnels d'une année sur l'autre

Le lien de chaque personne ne change jamais tant que la base existe : « Réinitialiser le tirage »
le conserve, et une personne retirée puis rajoutée avec le même nom retrouve son lien. Pour
l'année suivante, il suffit de réinitialiser le tirage, d'ajuster la liste et les notes, puis de
relancer.

Attention : `docker compose down -v` supprime la base, donc les liens. Utilise plutôt
`docker compose down` (sans `-v`). Le bouton « Télécharger la sauvegarde » de l'espace
organisateur donne une copie (sans les résultats du tirage ni l'historique). Pour la restaurer :

```bash
docker compose down
docker run --rm -v secret-santa_santa-data:/data -v "$PWD":/backup alpine \
  sh -c 'cp /backup/secret-santa-AAAA-MM-JJ.db /data/santa.db && chown 1000:1000 /data/santa.db'
docker compose up -d
```

(`secret-santa_santa-data` est le nom du volume : dossier du projet + `_santa-data`. Vérifie avec
`docker volume ls`.)

## Éviter les mêmes paires d'une année sur l'autre

Chaque tirage est rangé dans un historique (par année, avec les noms, donc même si la liste
change). Au tirage suivant, les paires des **N dernières éditions** (réglage de l'espace organisateur,
1 par défaut, 0 pour désactiver) sont interdites : personne ne retombe sur la même personne.

- Refaire le tirage la même année remplace l'historique de cette année, sans s'éviter lui-même.
- Si le groupe est trop petit ou les contraintes trop serrées pour éviter toutes les paires, le tirage
  les rend « très improbables » à la place et le signale.
- Une paire à la note 10 n'est jamais écartée.
- L'organisateur ne voit que « 2026 (6 paires) », jamais les paires elles-mêmes. La sauvegarde
  téléchargeable ne contient pas l'historique : après une restauration, l'évitement redémarre à zéro.
- Un tirage déjà fait avant l'arrivée de cette fonction est ajouté à l'historique au démarrage.

## Page du participant

Fond de nuit d'hiver avec neige, cadeau qui s'ouvre au clic, étiquette « De / À / Budget » qui en
sort, confettis. Sans JavaScript, le bouton fonctionne quand même (la page se recharge). Les
animations sont désactivées si l'appareil demande de réduire les mouvements.

## Budget

Le champ « Budget » de l'espace organisateur (par exemple `30 €`, ou `entre 20 et 30 €`) s'affiche
sur l'étiquette de chaque participant. Un simple nombre (`30`) devient `30 €`. Il peut être modifié
à tout moment, même après le tirage.

## Confidentialité

Les résultats ne sont affichés nulle part dans l'espace organisateur. Ils ne sont visibles
qu'avec le lien personnel de chacun (et l'organisateur possède tous les liens : s'il participe,
il ne doit ouvrir que le sien). Un participant doit cliquer pour révéler son résultat.
