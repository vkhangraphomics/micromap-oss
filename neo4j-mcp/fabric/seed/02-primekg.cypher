// Subgraph: primekg — disease↔drug and disease↔gene (PrimeKG slice)
UNWIND [
  {norm:'inflammatory bowel disease',  name:'inflammatory bowel disease'},
  {norm:'type 2 diabetes mellitus',    name:'type 2 diabetes mellitus'},
  {norm:'colorectal cancer',           name:'colorectal cancer'}
] AS d
MERGE (:Disease {name_normalized: d.norm, name: d.name});

UNWIND [
  {id:'D000069583', name:'Vedolizumab', drug_type:'biologic'},
  {id:'D007052',    name:'Infliximab',  drug_type:'biologic'},
  {id:'D008687',    name:'Metformin',   drug_type:'small_molecule'},
  {id:'D007328',    name:'Insulin',     drug_type:'biologic'},
  {id:'D000077',    name:'Cetuximab',   drug_type:'biologic'}
] AS x
MERGE (:Drug {drug_id: x.id, name: x.name, drug_type: x.drug_type});

UNWIND [
  {sym:'TNF',    ncbi:7124,  name:'tumor necrosis factor'},
  {sym:'IL6',    ncbi:3569,  name:'interleukin 6'},
  {sym:'PPARG',  ncbi:5468,  name:'peroxisome proliferator-activated receptor gamma'},
  {sym:'KRAS',   ncbi:3845,  name:'KRAS proto-oncogene'},
  {sym:'APC',    ncbi:324,   name:'APC regulator of WNT signaling'}
] AS g
MERGE (:Gene {ncbi_gene_id: g.ncbi, symbol: g.sym, name: g.name});

// Drug indications
UNWIND [
  {drug:'D000069583', dis:'inflammatory bowel disease'},
  {drug:'D007052',    dis:'inflammatory bowel disease'},
  {drug:'D008687',    dis:'type 2 diabetes mellitus'},
  {drug:'D007328',    dis:'type 2 diabetes mellitus'},
  {drug:'D000077',    dis:'colorectal cancer'}
] AS r
MATCH (dr:Drug {drug_id: r.drug})
MATCH (d:Disease {name_normalized: r.dis})
MERGE (dr)-[:INDICATED_FOR]->(d);

// Disease-gene associations  
UNWIND [
  {gene:7124, dis:'inflammatory bowel disease'},
  {gene:3569, dis:'inflammatory bowel disease'},
  {gene:5468, dis:'type 2 diabetes mellitus'},
  {gene:3845, dis:'colorectal cancer'},
  {gene:324,  dis:'colorectal cancer'}
] AS r
MATCH (g:Gene {ncbi_gene_id: r.gene})
MATCH (d:Disease {name_normalized: r.dis})
MERGE (d)-[:ASSOCIATED_WITH_GENE]->(g);
