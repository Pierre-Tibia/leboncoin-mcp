# Agent de recherche immobilière

Ce projet est un serveur MCP (fork de leboncoin-mcp) qui cherche des annonces sur Leboncoin et les évalue. Je suis débutant en développement : explique simplement.

## Méthode de recherche, dans cet ordre
1. Lis profil.md pour connaître les critères de recherche.
2. Cherche avec search_ads en appliquant les filtres les plus serrés possibles. Pas plus de 3 appels de recherche sans mon accord.
3. Pour chaque annonce, calcule le prix au m², le temps de marche jusqu'à la côte (walking_time_to_coast) et l'écart au marché (price_vs_market, en fournissant toujours surface_m2).
4. Fais un premier tri selon les critères du profil. Ce sont les annonces retenues.
5. Pour chaque annonce retenue publiée par un professionnel, vérifie la localisation : get_ad, puis get_user pour obtenir le site de l'agence, puis retrouve le bien sur ce site (référence, prix, surface) et relève le quartier ou l'adresse. Recalcule alors le temps de marche et l'écart au marché sur cette position.
6. Présente un tableau trié du plus intéressant au moins intéressant, avec le lien de chaque annonce.

## Règles de présentation
- Si la localisation n'a pas pu être vérifiée, écris « position non vérifiée » et ne présente pas ses chiffres comme sûrs.
- Le temps de marche est calculé à vol d'oiseau : rappelle qu'il est sous-estimé.
- Si price_vs_market renvoie reliable: false, dis-le.
- Signale les mentions « à rénover », « travaux », « viager », « occupé » ou « loué ».
- Reste discret avec Leboncoin : pas d'appels inutiles.

## Quand tu modifies le code
- Présente ton plan et attends mon accord avant de modifier quoi que ce soit.
- Dans ton plan, indique la taille du travail (petit, moyen ou gros) et les moments où tu auras besoin de mon accord.
- Ne modifie pas requirements.txt, et touche le moins possible à server.py. Les nouveaux tools vont dans des fichiers séparés.
- N'ajoute aucune dépendance sans me demander.
- Ajoute des tests, lance-les tous, et ne commite rien sans mon accord.
- Dans ton rapport final, indique pour chaque point demandé où il se trouve dans le code et quel test le vérifie.

