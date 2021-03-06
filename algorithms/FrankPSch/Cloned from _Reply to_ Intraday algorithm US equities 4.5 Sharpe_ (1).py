import math
import numpy as np
import pandas as pd
import scipy as sp

import statsmodels.api as smapi
from sklearn.covariance import OAS
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn import preprocessing

from quantopian.algorithm import attach_pipeline, pipeline_output
from quantopian.pipeline import Pipeline
from quantopian.pipeline.data import morningstar as mstar
from quantopian.pipeline.data.builtin import USEquityPricing
from quantopian.pipeline.filters.morningstar import Q1500US
from quantopian.pipeline.classifiers.morningstar import Sector

#import quantopian.experimental.optimize as opt
import quantopian.optimize as opt

#from quantopian.pipeline.filters.eventvestor import IsAnnouncedAcqTarget
#from quantopian.pipeline.data.eventvestor import EarningsCalendar
from quantopian.pipeline.factors.eventvestor import (
    BusinessDaysUntilNextEarnings,
    BusinessDaysSincePreviousEarnings
    )

def make_pipeline():
    minprice = USEquityPricing.close.latest > 5
    #not_announced_acq_target = ~IsAnnouncedAcqTarget()
    #pipe = Pipeline(screen=Q1500US() & minprice & not_announced_acq_target)
    pipe = Pipeline(screen=Q1500US() & minprice)

    sectors = Sector()
    pipe.add(sectors, 'sector')
    pipe.add(BusinessDaysSincePreviousEarnings(), 'PE')
    return pipe

def initialize(context):
    # default volume share slippage model
    #set_slippage(slippage.FixedSlippage(spread=0.00))
    set_slippage(slippage.VolumeShareSlippage(volume_limit=0.025, price_impact=0.1))

    # set the cost of trades $0.001 per share, with a minimum trade cost of $5
    #set_commission(commission.PerShare(cost=0.001, min_trade_cost=0))
    set_commission(commission.PerShare(cost=0.001, min_trade_cost=5.0))

    context.sectorStocks = {}
    context.stocks = None
    context.alphas = None
    context.betas = None
    
    context.use_stop_loss = False

    context.sector_ids = [ Sector.BASIC_MATERIALS,
                           Sector.CONSUMER_CYCLICAL,
                           Sector.FINANCIAL_SERVICES,
                           Sector.REAL_ESTATE,
                           Sector.CONSUMER_DEFENSIVE,
                           Sector.HEALTHCARE,
                           Sector.UTILITIES,
                           Sector.COMMUNICATION_SERVICES,
                           Sector.ENERGY,
                           Sector.INDUSTRIALS,
                           Sector.TECHNOLOGY ]

    context.leverage = 10. # was 1.
    context.days = 45
    context.counter = 2
    schedule_function(trade_sectors, 
                      date_rules.every_day(), 
                      time_rules.market_open(minutes=60))
    
    schedule_function(close_all, 
                      date_rules.every_day(), 
                      time_rules.market_close(minutes=30))
    
    schedule_function(update_chart, 
                      date_rules.every_day(), 
                      time_rules.market_close(minutes=1))
    
    attach_pipeline(make_pipeline(), "Q1500")

    # Initialize Stop Loss Manager
    if context.use_stop_loss:
        context.SL_Manager = StopLoss_Manager(pct_init=0.005, pct_trail=0.05)
        schedule_function(context.SL_Manager.manage_orders,
                          date_rules.every_day(),
                          time_rules.market_open())

def handle_data(context, data):
    pass

def before_trading_start(context, data):
    context.screener = pipeline_output("Q1500")
    context.screener = context.screener[context.screener['PE'] > 2].index

    # proceed every 45th day only
    if context.days < 45:
        context.days += 1
        return
    context.days = 0

    context.output = pipeline_output("Q1500")
    context.sectorStocks.clear()
    context.sectorStocks[Sector.BASIC_MATERIALS] = get_cluster(context, data, context.output[context.output.sector == Sector.BASIC_MATERIALS].index)    
    context.sectorStocks[Sector.CONSUMER_CYCLICAL]= get_cluster(context, data, context.output[context.output.sector == Sector.CONSUMER_CYCLICAL].index)    
    context.sectorStocks[Sector.CONSUMER_DEFENSIVE]= get_cluster(context, data, context.output[context.output.sector == Sector.CONSUMER_DEFENSIVE].index)
    context.sectorStocks[Sector.FINANCIAL_SERVICES]= get_cluster(context, data, context.output[context.output.sector == Sector.FINANCIAL_SERVICES].index)
    context.sectorStocks[Sector.REAL_ESTATE] = get_cluster(context, data, context.output[context.output.sector == Sector.REAL_ESTATE].index)
    context.sectorStocks[Sector.HEALTHCARE] = get_cluster(context, data, context.output[context.output.sector == Sector.HEALTHCARE].index)
    context.sectorStocks[Sector.UTILITIES] = get_cluster(context, data, context.output[context.output.sector == Sector.UTILITIES].index)
    context.sectorStocks[Sector.COMMUNICATION_SERVICES] = get_cluster(context, data, context.output[context.output.sector == Sector.COMMUNICATION_SERVICES].index)
    context.sectorStocks[Sector.ENERGY] = get_cluster(context, data, context.output[context.output.sector == Sector.ENERGY].index)
    context.sectorStocks[Sector.INDUSTRIALS]= get_cluster(context, data, context.output[context.output.sector == Sector.INDUSTRIALS].index)
    context.sectorStocks[Sector.TECHNOLOGY] = get_cluster(context, data, context.output[context.output.sector == Sector.TECHNOLOGY].index)
    
def get_cluster(context, data, stocks):
    return stocks
     
def trade_sectors(context, data):
    context.stocks = None
    context.alphas = None
    context.betas = None
    context.sectors = {}

    for sector_id in context.sector_ids:
        #if sector_id not in context.sectorStocks or len(context.sectorStocks[sector_id]) < 30:
        #    continue
        
        stocks, alphas, betas = find_weights(context, data, context.sectorStocks[sector_id])
        
        #if stocks is None:
        #    continue
 
        if context.stocks is None:
            context.stocks = stocks
            context.alphas = alphas
            context.betas = betas
        else:
            context.stocks = np.hstack((context.stocks, stocks))
            context.alphas = np.hstack((context.alphas, alphas))

            zero1 = np.zeros((context.betas.shape[0], betas.shape[1]))
            zero2 = np.zeros((betas.shape[0], context.betas.shape[1]))
            context.betas = np.hstack((context.betas, zero1))
            betas = np.hstack((zero2, betas))
            context.betas = np.vstack((context.betas, betas))

        for sid in context.stocks:
            context.sectors[sid] = sector_id

    if context.stocks is None:
        return

    todays_universe = context.stocks
    N = context.betas.shape[1]
    M = context.betas.shape[0]
    names = [str(i) for i in range(0, N)]

    # Define constraints
    constraints = []

    risk_factor_exposures = pd.DataFrame(context.betas, index=todays_universe, columns=names)
    neutralize_risk_factors = opt.FactorExposure(
        loadings=risk_factor_exposures,
        min_exposures=pd.Series([-0.01] * N, index=names),
        max_exposures=pd.Series([0.01] * N, index=names))
    constraints.append(neutralize_risk_factors)

    constraints.append(opt.PositionConcentration.with_equal_bounds(min=-10./M, max=10./M))

    constraints.append(opt.MaxGrossExposure(1.0))
    
    constraints.append(opt.DollarNeutral(0.0001))

    sector_neutral = opt.NetPartitionExposure.with_equal_bounds(labels=context.sectors, min=-0.0001, max=0.0001)
    constraints.append(sector_neutral)

    # Place orders
    objective = opt.MaximizeAlpha(pd.Series(-context.alphas, index=todays_universe))
    order_optimal_portfolio(objective=objective, constraints=constraints)

    # Stop Loss Manager after creating new orders  
    if context.use_stop_loss: context.SL_Manager.manage_orders(context, data)
                
def find_weights(context, data, stocks):
    prices = data.history(stocks, "price", 90, "1d")
    prices = prices.dropna(axis=1)
    
    dropsids = []
    for sid in prices:
        if sid not in context.screener:
            dropsids.append(sid)
    
    prices = prices.drop(dropsids, axis=1)

    logP = np.log(prices.values)
    diff = np.diff(logP, axis=0)
    factors = PCA(0.9,whiten=False).fit_transform(diff)
    model = smapi.OLS(diff, smapi.add_constant(factors)).fit()
    betas = model.params.T[:, 1:]
    model = smapi.GLS(diff[-1, :], betas, weights=1. / np.var(diff, axis=0)).fit()
    
    return prices.columns.values, sp.stats.zscore(model.resid), betas
   
def close_all(context, data):
    # close unfulfilled orders
    os = get_open_orders()
    for ol in os.values():
        for o in ol:
            cancel_order(o)
    
    # close open positions
    for sid in context.portfolio.positions:
        order_target(sid, 0)

def update_chart(context,data):
    # plot leverage
    record(leverage = context.account.leverage)

    # plot longs and shorts
    longs = shorts = 0
    for position in context.portfolio.positions.itervalues():        
        if position.amount > 0:
            longs += 1
        if position.amount < 0:
            shorts += 1
    record(long_lever=longs, short_lever=shorts)
    
#-------------------------------------------------------------------------------------
# StopLoss_Manager
# https://www.quantopian.com/posts/how-to-manage-stop-loss
class StopLoss_Manager:
    """
    Class to manage to stop-orders for any open position or open (non-stop)-order. This will be done for long- and short-positions.
    
    Parameters:  
        pct_init (optional),
        pct_trail (optional),
        (a detailed description can be found in the set_params function)
              
    Example Usage:
        context.SL = StopLoss_Manager(pct_init=0.005, pct_trail=0.03)
        context.SL.manage_orders(context, data)
    """
                
    def set_params(self, **params):
        """
        Set values of parameters:
        
        pct_init (optional float between 0 and 1):
            - After opening a new position, this value 
              is the percentage above or below price, 
              where the first stop will be place. 
        pct_trail (optional float between 0 and 1):
            - For any existing position the price of the stop 
              will be trailed by this percentage.
        """
        additionals = set(params.keys()).difference(set(self.params.keys()))
        if len(additionals)>1:
            log.warn('Got additional parameter, which will be ignored!')
            del params[additionals]
        self.params.update(params)
       
    def manage_orders(self, context, data):
        """
        This will:
            - identify any open positions and orders with no stop
            - create new stop levels
            - manage existing stop levels
            - create StopOrders with appropriate price and amount
        """        
        self._refresh_amounts(context)
                
        for sec in self.stops.index:
            cancel_order(self.stops['id'][sec])
            if self._np.isnan(self.stops['price'][sec]):
                stop = (1-self.params['pct_init'])*data.current(sec, 'close')
            else:
                o = self._np.sign(self.stops['amount'][sec])
                new_stop = (1-o*self.params['pct_trail'])*data.current(sec, 'close')
                stop = o*max(o*self.stops['price'][sec], o*new_stop)
                
            self.stops.loc[sec, 'price'] = stop           
            self.stops.loc[sec, 'id'] = order(sec, -self.stops['amount'][sec], style=StopOrder(stop))

    def __init__(self, **params):
        """
        Creating new StopLoss-Manager object.
        """
        self._import()
        self.params = {'pct_init': 0.01, 'pct_trail': 0.03}
        self.stops = self._pd.DataFrame(columns=['amount', 'price', 'id'])        
        self.set_params(**params)        
    
    def _refresh_amounts(self, context):
        """
        Identify open positions and orders.
        """
        # Reset position amounts
        self.stops.loc[:, 'amount'] = 0.
        
        # Get open orders and remember amounts for any order with no defined stop.
        open_orders = get_open_orders()
        new_amounts = []
        for sec in open_orders:
            for order in open_orders[sec]:
                if order.stop is None:
                    new_amounts.append((sec, order.amount))                
            
        # Get amounts from portfolio positions.
        for sec in context.portfolio.positions:
            new_amounts.append((sec, context.portfolio.positions[sec].amount))
            
        # Sum amounts up.
        for (sec, amount) in new_amounts:
            if not sec in self.stops.index:
                self.stops.loc[sec, 'amount'] = amount
            else:
                self.stops.loc[sec, 'amount'] = +amount
            
        # Drop securities, with no position/order any more. 
        drop = self.stops['amount'] == 0.
        self.stops.drop(self.stops.index[drop], inplace=True)
        
    def _import(self):
        """
        Import of needed packages.
        """
        import numpy
        self._np = numpy
        
        import pandas
        self._pd = pandas

#-------------------------------------------------------------------------------------