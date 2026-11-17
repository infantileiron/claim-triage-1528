/** @typedef {{id:number,code:string,amount:number,distance:number,holiday:boolean,label:0|1|null,note:string,claimed_on:string}} Claim */
/** @typedef {{id:number,name:string,threshold:number,state:string,result:object,source:string,trigger_id:number|null}} Training */
/** @typedef {{id:number,claim_id:number,training_id:number,score:number,decision:string,status:string}} Review */
/** @typedef {{id:number,window_end:string,state:string,alert:boolean,revision:number,baseline_training_id:number|null,pending_reason:string,metrics:object|null,error:string,updated:string}} DriftWindow */
/** @typedef {{id:number,training_id:number|null,state:string,round_open:boolean,basis_window_ids:number[],streak_start:string,baseline_training_id:number|null,cancel_reason:string,created:string,updated:string,training?:Training}} DriftTrigger */
