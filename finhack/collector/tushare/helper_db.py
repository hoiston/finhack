import re
import sys
import time
import datetime
import traceback
import pandas as pd

from finhack.library.db import DB
from finhack.library.alert import alert
from finhack.library.monitor import tsMonitor
import finhack.library.log as Log

class tsDBHelper:
    """
    Tushare数据库辅助类
    使用DB类提供的统一数据库接口，支持DuckDB和MySQL
    """
    
    def getAllAStockIndex(pro=None, db='default'):
        sql='select * from astock_index_basic'
        data=DB.select_to_df(sql, db)
        return data
    
    def getAllAStock(fromDB=True, pro=None, db='default'):
        if fromDB:
            sql='select * from astock_basic'
            data=DB.select_to_df(sql, db)
             
        else:
            all_stock=[]
            data = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,list_date,delist_date,is_hs')
            all_stock.append(data)
            data = pro.stock_basic(exchange='', list_status='D', fields='ts_code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,list_date,delist_date,is_hs')
            all_stock.append(data)
            data = pro.stock_basic(exchange='', list_status='P', fields='ts_code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,list_date,delist_date,is_hs')
            all_stock.append(data)
            data=pd.concat(all_stock, axis=0, ignore_index=True)
        return data
  
  
    def setIndex(table, db='default'):
        """
        为表创建索引
        支持DuckDB和MySQL
        """
        # 获取数据库适配器类型
        adapter = DB.get_adapter(db)
        adapter_type = adapter.__class__.__name__
        
        index_list=['ts_code', 'end_date', 'trade_date']
        for index in index_list:
            if adapter_type == 'DuckDBAdapter':
                # DuckDB索引语法
                sql = f"CREATE INDEX IF NOT EXISTS idx_{table}_{index} ON {table}({index})"
            else:
                # MySQL索引语法
                sql = f"CREATE INDEX {index} ON {table} ({index}(10))"
            
            try:
                DB.exec(sql, db)
            except Exception as e:
                Log.logger.warning(f"为表 {table} 创建索引 {index} 失败: {str(e)}")
  
    def getAllFund(db='default'):
        sql='select * from fund_basic'
        data=DB.select_to_df(sql, db)
        return data      
       
    # 重新获取数据 
    def getDataAndReplace(pro, api, table, db):
        DB.exec(f"DROP TABLE IF EXISTS {table}_tmp", db)
        engine = DB.get_db_engine(db)
        f = getattr(pro, api)
        data = f()
        
        # 使用DB类的to_sql方法
        DB.to_sql(data, f"{table}_tmp", db, 'replace')
        
        # 重命名表
        adapter = DB.get_adapter(db)
        adapter_type = adapter.__class__.__name__
        
        if adapter_type == 'DuckDBAdapter':
            # DuckDB重命名语法
            DB.exec(f"ALTER TABLE {table}_tmp RENAME TO {table}", db)
        else:
            # MySQL重命名语法
            DB.exec(f"RENAME TABLE {table} TO {table}_old", db)
            DB.exec(f"RENAME TABLE {table}_tmp TO {table}", db)
            DB.exec(f"DROP TABLE IF EXISTS {table}_old", db)
        
        tsDBHelper.setIndex(table, db)
    
    
    # 根据最后日期获取数据
    def getDataWithLastDate(pro, api, table, db, filed='trade_date', ts_code=''):
        engine = DB.get_db_engine(db)
        lastdate = tsDBHelper.getLastDateAndDelete(table=table, filed=filed, ts_code=ts_code, db=db)
        begin = datetime.datetime.strptime(lastdate, "%Y%m%d")
        end = datetime.datetime.now()
        i=0
        while i<(end - begin).days+1:
            day = begin + datetime.timedelta(days=i)
            day=day.strftime("%Y%m%d")
            f = getattr(pro, api)
            try_times=0
            while True:
                try:
                    df=pd.DataFrame()
                    if(ts_code==''):
                        if filed=='trade_date':
                            df=f(trade_date=day)
                        elif filed=='ann_date':
                            df=f(ann_date=day)
                        elif filed=='end_date':
                            df=f(end_date=day)
                        elif filed=='date':
                            df=f(date=day)
                        elif filed=='nav_date':
                            df=f(nav_date=day)
                        elif filed=='cal_date':
                            df=f(cal_date=day)
                        else:
                            alert.send(api, '函数异常', filed+"未处理")
                    else:
                        if filed=='trade_date':
                            df=f(trade_date=day, ts_code=ts_code)
                        elif filed=='ann_date':
                            df=f(ann_date=day, ts_code=ts_code)
                        elif filed=='end_date':
                            df=f(end_date=day, ts_code=ts_code)  
                        elif filed=='date':
                            df=f(date=day, ts_code=ts_code)  
                        elif filed=='nav_date':
                            df=f(nav_date=day, ts_code=ts_code)
                        elif filed=='cal_date':
                            df=f(cal_date=day)
                        else:
                            alert.send(api, '函数异常', filed+"未处理")
                    
                    if(not df.empty):
                        # 使用DB类的to_sql方法
                        DB.to_sql(df, table, db, 'append')
                    break
                except Exception as e:
                    if "每分钟最多访问" in str(e):
                        Log.logger.warning(api+":触发限流，等待重试。\n"+str(e))
                        time.sleep(15)
                        continue
                    
                    if "每天最多访问" in str(e) or "每小时最多访问" in str(e):
                        Log.logger.warning(api+":今日权限用完。\n"+str(e))
                        return
                         
                   
                    elif "您没有访问该接口的权限" in str(e):
                        Log.logger.warning(api+":没有访问该接口的权限。\n"+str(e))
                        return
                    
                    else:
                        if try_times<10:
                            try_times=try_times+1
                            Log.logger.error(api+":函数异常，等待重试。\n"+str(e))
                            time.sleep(15)
                            continue
                        else:                        
                            info = traceback.format_exc()
                            alert.send(api, '函数异常', str(info))
                            
                            Log.logger.error(api+"\n"+info)
                            return

            i=i+1        
            
    
    def getDataWithCodeAndClear(pro, api, table, db):
        DB.exec(f"DROP TABLE IF EXISTS {table}_tmp", db)
        engine = DB.get_db_engine(db)
        data = tsDBHelper.getAllAStock(True, pro, db)
        stock_list = data['ts_code'].tolist()
        f = getattr(pro, api)
        for code in stock_list:
            try_times=0
            while True:
                try:
                    df = f(ts_code=code)
                    # 使用DB类的to_sql方法
                    DB.to_sql(df, f"{table}_tmp", db, 'append')
                    break
                except Exception as e:
                    if "每分钟最多访问" in str(e):
                        Log.logger.warning(api+":触发限流，等待重试。\n"+str(e))
                        time.sleep(15)
                        continue
                    
                    if "每天最多访问" in str(e) or "每小时最多访问" in str(e):
                        Log.logger.warning(api+":今日权限用完。\n"+str(e))
                        return
                   
                    elif "您没有访问该接口的权限" in str(e):
                        Log.logger.warning(api+":没有访问该接口的权限。\n"+str(e))
                        return
                    
                    else:
                        if try_times<10:
                            try_times=try_times+1
                            Log.logger.error(api+":函数异常，等待重试。\n"+str(e))
                            time.sleep(15)
                            continue
                        else:                            
                            info = traceback.format_exc()
                            alert.send(api, '函数异常', str(info))
                            Log.logger.error(str(info))
                            return
     
        # 重命名表
        adapter = DB.get_adapter(db)
        adapter_type = adapter.__class__.__name__
        
        if adapter_type == 'DuckDBAdapter':
            # DuckDB重命名语法
            DB.exec(f"ALTER TABLE {table}_tmp RENAME TO {table}", db)
        else:
            # MySQL重命名语法
            DB.exec(f"RENAME TABLE {table} TO {table}_old", db)
            DB.exec(f"RENAME TABLE {table}_tmp TO {table}", db)
            DB.exec(f"DROP TABLE IF EXISTS {table}_old", db)
            
        tsDBHelper.setIndex(table, db)
        
    
    # 查一下最后的数据是哪天
    def getLastDateAndDelete(table, filed, ts_code="", db='default'):
        # 检查表是否存在
        adapter = DB.get_adapter(db)
        adapter_type = adapter.__class__.__name__
        
        if adapter_type == 'DuckDBAdapter':
            # DuckDB检查表是否存在
            result = DB.select_to_list(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'", db)
            if not result:
                return '20100101'  # 如果表不存在，返回一个较早的日期
        else:
            # MySQL检查表是否存在
            result = DB.select_to_list(f"SHOW TABLES LIKE '{table}'", db)
            if not result:
                return '20100101'  # 如果表不存在，返回一个较早的日期
        
        # 获取最后日期
        if ts_code == "":
            sql = f"SELECT MAX({filed}) as max_date FROM {table}"
        else:
            sql = f"SELECT MAX({filed}) as max_date FROM {table} WHERE ts_code='{ts_code}'"
            
        result = DB.select_to_list(sql, db)
        
        if not result or not result[0]['max_date']:
            return '20100101'  # 如果没有数据，返回一个较早的日期
        
        max_date = result[0]['max_date']
        
        # 删除最后一天的数据（为了避免不完整）
        if ts_code == "":
            DB.delete(f"DELETE FROM {table} WHERE {filed}='{max_date}'", db)
        else:
            DB.delete(f"DELETE FROM {table} WHERE {filed}='{max_date}' AND ts_code='{ts_code}'", db)
            
        return max_date