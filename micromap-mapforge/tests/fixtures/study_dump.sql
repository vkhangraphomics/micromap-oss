-- MySQL-ish dump
DROP TABLE IF EXISTS `studies`;
CREATE TABLE `studies` (
  `tax_id` INT,
  `organism` VARCHAR(255),
  `condition` VARCHAR(255),
  `log2fc` FLOAT,
  `pvalue` FLOAT
);

INSERT INTO `studies` VALUES (562,'Escherichia coli','Crohns Disease',1.3,0.001);
INSERT INTO `studies` VALUES (1496,'Clostridium difficile','Ulcerative Colitis',-2.1,0.0003);
INSERT INTO `studies` VALUES (562,'Escherichia coli','Ulcerative Colitis',0.8,0.02);
