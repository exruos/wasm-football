CREATE TABLE league (
	id	INTEGER PRIMARY KEY,
	country_id	INTEGER,
	name	TEXT UNIQUE,
	FOREIGN KEY(country_id) REFERENCES country(id)
);
INSERT INTO league VALUES(1,1,'Belgium Jupiler league');
INSERT INTO league VALUES(1729,1729,'England Premier league');
INSERT INTO league VALUES(4769,4769,'France Ligue 1');
INSERT INTO league VALUES(7809,7809,'Germany 1. Bundesliga');
INSERT INTO league VALUES(10257,10257,'Italy Serie A');
INSERT INTO league VALUES(13274,13274,'Netherlands Eredivisie');
INSERT INTO league VALUES(15722,15722,'Poland Ekstraklasa');
INSERT INTO league VALUES(17642,17642,'Portugal Liga ZON Sagres');
INSERT INTO league VALUES(19694,19694,'Scotland Premier league');
INSERT INTO league VALUES(21518,21518,'Spain LIGA BBVA');
INSERT INTO league VALUES(24558,24558,'Switzerland Super league');
