# -*- coding: utf-8 -*-
"""
GREE México 批量发票折扣舍入修正 —— 一键处理脚本
用法:  python procesar_facturas.py  <原始模版.csv>

背景: 开票系统会把 Descuento(折扣) 从2位小数自动四舍五入到1位, 产生误差。
本脚本在【单价只能2位小数】的约束下, 主动把折扣压到1位并调整单价/相关行,
使发票的 ∑IVA、∑Total 尽量与原始一致, 最后按发票拆成两份输出。
"""
import sys, itertools
from math import gcd
from functools import reduce
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd

# ==== 列名(如模版变动在此调整) ====
INV='No. Factura'; CANT='Cantidad'; PU='Precio Unitario'; SUB='Subtotal del Concepto'
DESC='Descuento del Concepto'; TASA='Tasa'; IVA='IVA del Concepto'; TOT='Total del Concepto'
WINDOW=60   # 发票级搜索时每行税基上下浮动的分数范围

def D(x): x=str(x).strip(); return Decimal(x) if x else Decimal('0')
def fmt(c): return format(Decimal(c)/100,'f')                       # 分->金额字符串
def ivac(Tc,tasa): return int((Decimal(Tc)/100*tasa).quantize(Decimal('0.01'),ROUND_HALF_UP)*100)

def realize(Tc,C,Dc0):
    """给定目标税基Tc(分)与数量C, 返回最接近原折扣Dc0的(折扣1位, 单价2位); 无解返回None"""
    best=None
    for k in range(-1000,1001):
        cand=(Dc0//10+k)*10                # 1位小数折扣 = 10分整数倍
        if cand<0: continue
        if (Tc+cand)>=0 and (Tc+cand)%C==0: # 单价须2位 => 小计(分)须被数量整除
            m=abs(cand-Dc0)
            if best is None or m<best[0]: best=(m,cand,(Tc+cand)//C)
    return best

def classify(C,Dc):
    """nochange / A(可行级精确) / B(2位下不可行)"""
    if Dc==0 or Dc%10==0: return 'nochange'
    return 'A' if Dc%gcd(10,C)==0 else 'B'

def main(path):
    df=pd.read_csv(path,dtype=str,keep_default_na=False)
    out=df.copy()

    # ---------- 要求1: 公式校验 ----------
    anomalies=[]
    for i,r in df.iterrows():
        C=D(r[CANT]);P=D(r[PU]);S=D(r[SUB]);De=D(r[DESC]);Ta=D(r[TASA]);Iv=D(r[IVA]);To=D(r[TOT])
        if (C*P).quantize(Decimal('0.01'))!=S: anomalies.append((i+2,'Subtotal≠数量×单价'))
        elif ((S-De)*Ta).quantize(Decimal('0.01'),ROUND_HALF_UP)!=Iv: anomalies.append((i+2,'IVA≠round((小计-折扣)×税率)'))
        elif (S-De+Iv).quantize(Decimal('0.01'))!=To: anomalies.append((i+2,'Total≠小计-折扣+IVA'))

    # ---------- 逐行分类 + 缓存整数分 ----------
    meta={}
    for i,r in df.iterrows():
        C=int(D(r[CANT])); PUc=int((D(r[PU])*100).to_integral_value())
        Dc=int((D(r[DESC])*100).to_integral_value()); tasa=D(r[TASA])
        meta[i]=dict(C=C,PUc=PUc,Dc=Dc,tasa=tasa,T=C*PUc-Dc,g=gcd(10,C),
                     ivaF=int((D(r[IVA])*100).to_integral_value()),
                     totF=int((D(r[TOT])*100).to_integral_value()),cls=classify(C,Dc))

    def apply_line(i,Tc):     # 按目标税基改写该行(折扣1位/单价2位, 重算IVA/Total)
        m=meta[i]; res=realize(Tc,m['C'],m['Dc']); niva=ivac(Tc,m['tasa'])
        out.at[i,PU]=fmt(res[2]); out.at[i,DESC]=fmt(res[1]); out.at[i,SUB]=fmt(res[2]*m['C'])
        out.at[i,IVA]=fmt(niva); out.at[i,TOT]=fmt(Tc+niva)

    # ---------- 按发票判定与处理 ----------
    ready_inv=[]; round_only_inv=[]; balanced_inv=[]; pending_inv=[]
    for inv in dict.fromkeys(df[INV]):
        idx=[i for i in df.index if df.loc[i,INV]==inv]
        has_B=any(meta[i]['cls']=='B' for i in idx)
        if not has_B:
            # 干净发票: A行税基精确不变, 折扣自动挪到最近合法1位小数(挑选逻辑在realize内, 含"不为负"闸), 其余行不动
            for i in idx:
                if meta[i]['cls']=='A':
                    apply_line(i, meta[i]['T'])
            ready_inv.append(inv); continue

        # 含B: 先试 round-only(只把折扣舍到1位, 单价一律不动); 仅当整张 ∑IVA、∑Total 都守住才采用
        idxm=[meta[i] for i in idx]
        sumT=sum(m['T'] for m in idxm); sumIVAf=sum(m['ivaF'] for m in idxm)
        roDc=[(m['Dc']+5)//10*10 for m in idxm]                       # 折扣就近舍到1位(半进)
        roT =[m['C']*m['PUc']-roDc[k] for k,m in enumerate(idxm)]     # 单价不动 => 小计不动
        if (sum(roT)==sumT
            and sum(ivac(roT[k],idxm[k]['tasa']) for k in range(len(idx)))==sumIVAf
            and all(roDc[k]>=0 and roT[k]>=0 for k in range(len(idx)))):
            for k,i in enumerate(idx):
                niva=ivac(roT[k],idxm[k]['tasa'])
                out.at[i,DESC]=fmt(roDc[k]); out.at[i,IVA]=fmt(niva); out.at[i,TOT]=fmt(roT[k]+niva)
                # PU / Subtotal 保持原样, 不改
            round_only_inv.append(inv); ready_inv.append(inv); continue

        # 否则: 调价平衡(∑税基, ∑IVA 同时守恒)
        lines=[dict(i=i,**meta[i]) for i in idx]
        G=reduce(gcd,[l['g'] for l in lines])
        sol=None
        if len(lines)>1 and sumT%G==0:
            cand=[[l['T']+k for k in range(-WINDOW,WINDOW+1)
                   if (l['T']+k)%l['g']==0 and l['T']+k>=0 and realize(l['T']+k,l['C'],l['Dc'])] for l in lines]
            if all(cand):
                for combo in itertools.product(*cand):
                    if sum(combo)!=sumT: continue
                    if sum(ivac(t,lines[k]['tasa']) for k,t in enumerate(combo))!=sumIVAf: continue
                    dist=sum(abs(t-lines[k]['T']) for k,t in enumerate(combo))
                    if sol is None or dist<sol[0]: sol=(dist,combo)
        if sol:
            for k,l in enumerate(lines): apply_line(l['i'],sol[1][k])
            balanced_inv.append(inv); ready_inv.append(inv)
        else:
            pending_inv.append(inv)    # 无解 -> 原样保留(out 不改动这些行)

    # ---------- 输出 ----------
    stem=path.rsplit('/',1)[-1].rsplit('.',1)[0]
    ready=out[out[INV].isin(ready_inv)]
    pend =out[out[INV].isin(pending_inv)].copy()   # 除下面清空Mail外, 与原始一致
    if 'Mail' in pend.columns:
        pend['Mail']=''                            # 清空客户邮箱, 防误上传把错误发票发给客户
    ready.to_csv(f"{stem}__lista_para_subir.csv",index=False)
    pend.to_csv(f"{stem}__pendiente.csv",index=False)

    # ---------- 报告 ----------
    print(f"总发票 {df[INV].nunique()} 张 / {len(df)} 行")
    print(f"公式校验异常: {len(anomalies)} 行", *[f"  行{r}: {m}" for r,m in anomalies], sep="\n" if anomalies else "")
    print(f"\n可上传 (∑IVA、∑Total 每张零误差): {len(ready_inv)} 张 / {len(ready)} 行  -> {stem}__lista_para_subir.csv")
    print(f"  其中仅折扣舍入、单价不动: {len(round_only_inv)} 张 {sorted(round_only_inv,key=str)}")
    print(f"  其中调价平衡救回:        {len(balanced_inv)} 张 {sorted(balanced_inv,key=str)}")
    print(f"待处理 (2位下无解, 原样保留·已清空Mail): {len(pending_inv)} 张 / {len(pend)} 行  -> {stem}__pendiente.csv")
    if pending_inv: print(f"  待处理发票: {sorted(pending_inv,key=str)}")

if __name__=='__main__':
    main(sys.argv[1] if len(sys.argv)>1 else 'Template_Factura__0__8__16__.csv')
