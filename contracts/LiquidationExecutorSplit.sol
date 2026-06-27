// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title APEX_OMEGA Liquidation Executor
 * @notice Production-grade smart contract for profitable Aave V3 liquidations on Polygon
 * @dev Integrates Balancer V2 flash loans (0% fee) with Aave V3 liquidations.
 */

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IBalancerVault {
    function flashLoan(
        address recipient,
        address[] memory tokens,
        uint256[] memory amounts,
        bytes memory userData
    ) external;
}

interface IAavePool {
    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;
}

interface IUniswapV3Router {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params) external returns (uint256 amountOut);
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface ICurveRouter {
    function exchange(
        address pool,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        address receiver
    ) external returns (uint256 amountOut);
}

contract LiquidationExecutorSplit is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IBalancerVault public constant BALANCER_VAULT = IBalancerVault(0xBA12222222228d8Ba445958a75a0704d566BF2C8);
    IAavePool public constant AAVE_POOL = IAavePool(0x794a61358D6845594F94dc1DB02A252b5b4814aD);

    address public constant QUICKSWAP_V3_ROUTER = 0xf5b509bB0909a69B1c207E495f687a596C168E12;
    address public constant UNISWAP_V3_ROUTER = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address public constant SUSHISWAP_ROUTER = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;
    address public constant QUICKSWAP_V2_ROUTER = 0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;
    address public constant CURVE_ROUTER = 0x1d8b86e3D88cDb2d34688e87E72F388Cb541B7C8;
    address public profitReceiver;
    address public platformTreasury;

    uint256 public constant BPS_SCALE = 10_000;
    uint256 public constant OWNER_PROFIT_BPS = 3_000;
    uint256 public constant TREASURY_PROFIT_BPS = 7_000;

    enum Protocol {
        QUICKSWAP_V3,
        UNISWAP_V3,
        SUSHISWAP,
        QUICKSWAP_V2,
        CURVE
    }

    event LiquidationExecuted(
        address indexed user,
        address indexed collateralAsset,
        address indexed debtAsset,
        uint256 debtCovered,
        uint256 collateralReceived,
        uint256 profitUsd
    );

    event SwapExecuted(
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        Protocol protocol
    );

    event EmergencyWithdraw(address indexed token, address indexed to, uint256 amount);
    event ProfitReceiverUpdated(address indexed previousReceiver, address indexed newReceiver);
    event ProfitDispatched(address indexed token, address indexed receiver, uint256 amount);
    event PlatformTreasuryUpdated(address indexed previousTreasury, address indexed newTreasury);
    event ProfitSplit(
        address indexed token,
        address indexed ownerReceiver,
        address indexed treasuryReceiver,
        uint256 ownerAmount,
        uint256 treasuryAmount
    );

    struct LiquidationParams {
        address collateralAsset;
        address debtAsset;
        address user;
        uint256 debtToCover;
        uint256 minProfitBps;
        Protocol swapProtocol;
        uint24 swapFee;
        uint256 minDebtAmountOut;
        address curvePool;
        uint256 maxSlippageBps;
    }

    constructor(address _profitReceiver, address _platformTreasury) Ownable(msg.sender) {
        require(_profitReceiver != address(0), "Invalid profit receiver");
        require(_platformTreasury != address(0), "Invalid platform treasury");
        profitReceiver = _profitReceiver;
        platformTreasury = _platformTreasury;
        emit ProfitReceiverUpdated(address(0), _profitReceiver);
        emit PlatformTreasuryUpdated(address(0), _platformTreasury);
    }

    function executeLiquidation(LiquidationParams calldata params)
        external
        onlyOwner
        nonReentrant
    {
        address[] memory tokens = new address[](1);
        tokens[0] = params.debtAsset;

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = params.debtToCover;

        uint256 preDebtBalance = IERC20(params.debtAsset).balanceOf(address(this));
        bytes memory userData = abi.encode(params, preDebtBalance);

        BALANCER_VAULT.flashLoan(address(this), tokens, amounts, userData);
    }

    function receiveFlashLoan(
        IERC20[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external {
        require(msg.sender == address(BALANCER_VAULT), "Unauthorized");

        (LiquidationParams memory params, uint256 preDebtBalance) = abi.decode(userData, (LiquidationParams, uint256));
        require(tokens.length == 1 && amounts.length == 1 && feeAmounts.length == 1, "Invalid flash loan");
        require(address(tokens[0]) == params.debtAsset, "Unexpected debt asset");

        uint256 debtAmount = amounts[0];
        uint256 flashLoanFee = feeAmounts[0];
        uint256 preCollateralBalance = IERC20(params.collateralAsset).balanceOf(address(this));

        IERC20(params.debtAsset).forceApprove(address(AAVE_POOL), debtAmount);

        AAVE_POOL.liquidationCall(
            params.collateralAsset,
            params.debtAsset,
            params.user,
            debtAmount,
            false
        );

        uint256 collateralBalance = IERC20(params.collateralAsset).balanceOf(address(this));
        uint256 collateralReceived = collateralBalance > preCollateralBalance
            ? collateralBalance - preCollateralBalance
            : 0;

        emit LiquidationExecuted(
            params.user,
            params.collateralAsset,
            params.debtAsset,
            debtAmount,
            collateralReceived,
            0
        );

        uint256 debtReceived = _executeSwap(
            params.collateralAsset,
            params.debtAsset,
            collateralReceived,
            params.swapProtocol,
            params.swapFee,
            params.minDebtAmountOut,
            params.curvePool,
            params.maxSlippageBps
        );

        uint256 totalRepayment = debtAmount + flashLoanFee;
        require(debtReceived >= totalRepayment, "Insufficient funds to repay flash loan");

        IERC20(params.debtAsset).safeTransfer(address(BALANCER_VAULT), totalRepayment);

        uint256 profit = debtReceived - totalRepayment;
        uint256 minProfit = (debtAmount * params.minProfitBps) / 10000;

        require(profit >= minProfit, "Profit below minimum threshold");

        uint256 debtBalance = IERC20(params.debtAsset).balanceOf(address(this));
        require(debtBalance >= preDebtBalance + profit, "Profit balance mismatch");
        _dispatchProfit(params.debtAsset, profit);
    }

    function _executeSwap(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        Protocol protocol,
        uint24 fee,
        uint256 minDebtAmountOut,
        address curvePool,
        uint256 maxSlippageBps
    ) internal returns (uint256 amountOut) {
        require(maxSlippageBps <= 10000, "Invalid slippage");
        require(minDebtAmountOut > 0, "Minimum output required");
        if (protocol == Protocol.CURVE) {
            amountOut = _swapCurve(tokenIn, tokenOut, amountIn, minDebtAmountOut, curvePool);
        } else if (protocol == Protocol.QUICKSWAP_V3 || protocol == Protocol.UNISWAP_V3) {
            amountOut = _swapV3(tokenIn, tokenOut, amountIn, fee, minDebtAmountOut, protocol);
        } else {
            amountOut = _swapV2(tokenIn, tokenOut, amountIn, minDebtAmountOut, protocol);
        }

        emit SwapExecuted(tokenIn, tokenOut, amountIn, amountOut, protocol);
    }

    function _swapV3(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint24 fee,
        uint256 minAmountOut,
        Protocol protocol
    ) internal returns (uint256 amountOut) {
        address router = protocol == Protocol.QUICKSWAP_V3
            ? QUICKSWAP_V3_ROUTER
            : UNISWAP_V3_ROUTER;

        IERC20(tokenIn).forceApprove(router, amountIn);

        IUniswapV3Router.ExactInputSingleParams memory params = IUniswapV3Router.ExactInputSingleParams({
            tokenIn: tokenIn,
            tokenOut: tokenOut,
            fee: fee,
            recipient: address(this),
            deadline: block.timestamp + 300,
            amountIn: amountIn,
            amountOutMinimum: minAmountOut,
            sqrtPriceLimitX96: 0
        });

        amountOut = IUniswapV3Router(router).exactInputSingle(params);
    }

    function _swapV2(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        Protocol protocol
    ) internal returns (uint256 amountOut) {
        address router = protocol == Protocol.SUSHISWAP
            ? SUSHISWAP_ROUTER
            : QUICKSWAP_V2_ROUTER;

        IERC20(tokenIn).forceApprove(router, amountIn);

        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;

        uint256[] memory amounts = IUniswapV2Router(router).swapExactTokensForTokens(
            amountIn,
            minAmountOut,
            path,
            address(this),
            block.timestamp + 300
        );

        amountOut = amounts[amounts.length - 1];
    }

    function _swapCurve(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        address curvePool
    ) internal returns (uint256 amountOut) {
        require(curvePool != address(0), "Curve pool required");
        IERC20(tokenIn).forceApprove(CURVE_ROUTER, amountIn);
        amountOut = ICurveRouter(CURVE_ROUTER).exchange(
            curvePool,
            tokenIn,
            tokenOut,
            amountIn,
            minAmountOut,
            address(this)
        );
    }

    function emergencyWithdraw(address token, address to, uint256 amount)
        external
        onlyOwner
    {
        IERC20(token).safeTransfer(to, amount);
        emit EmergencyWithdraw(token, to, amount);
    }

    function setProfitReceiver(address newProfitReceiver) external onlyOwner {
        require(newProfitReceiver != address(0), "Invalid profit receiver");
        address previousReceiver = profitReceiver;
        profitReceiver = newProfitReceiver;
        emit ProfitReceiverUpdated(previousReceiver, newProfitReceiver);
    }

    function setPlatformTreasury(address newTreasury) external onlyOwner {
        require(newTreasury != address(0), "Invalid platform treasury");
        address previousTreasury = platformTreasury;
        platformTreasury = newTreasury;
        emit PlatformTreasuryUpdated(previousTreasury, newTreasury);
    }

    function withdrawAll(address token, address to) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransfer(to, balance);
        emit EmergencyWithdraw(token, to, balance);
    }

    function withdrawNative(address payable to, uint256 amount) external onlyOwner {
        to.transfer(amount);
    }

    function _dispatchProfit(address token, uint256 amount) internal {
        if (amount == 0) return;
        address receiver = profitReceiver;
        address treasury = platformTreasury;
        require(receiver != address(0), "Invalid profit receiver");
        require(treasury != address(0), "Invalid platform treasury");

        uint256 ownerAmount = (amount * OWNER_PROFIT_BPS) / BPS_SCALE;
        uint256 treasuryAmount = amount - ownerAmount;

        if (ownerAmount > 0) {
            IERC20(token).safeTransfer(receiver, ownerAmount);
            emit ProfitDispatched(token, receiver, ownerAmount);
        }
        if (treasuryAmount > 0) {
            IERC20(token).safeTransfer(treasury, treasuryAmount);
            emit ProfitDispatched(token, treasury, treasuryAmount);
        }

        emit ProfitSplit(token, receiver, treasury, ownerAmount, treasuryAmount);
    }

    receive() external payable {}
}
