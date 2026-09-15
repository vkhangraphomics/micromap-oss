// Subgraph: micromap — microbe→disease associations
CREATE CONSTRAINT mf_taxon_ncbi IF NOT EXISTS FOR (n:Taxon) REQUIRE n.ncbi_tax_id IS UNIQUE;
CREATE CONSTRAINT mf_disease_norm IF NOT EXISTS FOR (n:Disease) REQUIRE n.name_normalized IS UNIQUE;

UNWIND [
  {id:'853',  name:'Faecalibacterium prausnitzii'},
  {id:'239935',name:'Akkermansia muciniphila'},
  {id:'817',  name:'Bacteroides fragilis'},
  {id:'1496', name:'Clostridioides difficile'},
  {id:'216816',name:'Bifidobacterium longum'}
] AS t
MERGE (:Taxon {ncbi_tax_id: t.id, name: t.name});

UNWIND [
  {norm:'inflammatory bowel disease',  name:'inflammatory bowel disease'},
  {norm:'type 2 diabetes mellitus',    name:'type 2 diabetes mellitus'},
  {norm:'colorectal cancer',           name:'colorectal cancer'}
] AS d
MERGE (:Disease {name_normalized: d.norm, name: d.name});

UNWIND [
  {tax:'853',   dis:'inflammatory bowel disease', dir:'depleted',  fc:-2.1, p:1e-6},
  {tax:'1496',  dis:'inflammatory bowel disease', dir:'enriched',  fc:2.4,  p:1e-7},
  {tax:'239935',dis:'type 2 diabetes mellitus',   dir:'depleted',  fc:-1.2, p:8e-4},
  {tax:'216816',dis:'type 2 diabetes mellitus',   dir:'depleted',  fc:-0.9, p:1e-2},
  {tax:'817',   dis:'colorectal cancer',          dir:'enriched',  fc:1.6,  p:4e-5},
  {tax:'239935',dis:'colorectal cancer',          dir:'depleted',  fc:-0.8, p:3e-2}
] AS r
MATCH (t:Taxon {ncbi_tax_id: r.tax})
MATCH (d:Disease {name_normalized: r.dis})
MERGE (t)-[:ASSOCIATED_WITH_DISEASE {direction: r.dir, log2fc: r.fc, p_value: r.p}]->(d);
